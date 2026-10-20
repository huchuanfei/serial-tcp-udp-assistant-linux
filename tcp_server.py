# -*- coding: utf-8 -*-
"""TCP Server 网络调试工具
基于 tkinter，支持多客户端连接、HEX/ASCII 显示与发送模式（勾选选择）。
"""
import queue
import re
import socket
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

MAX_RECV = 65535


def bytes_to_hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def parse_hex(text: str) -> bytes:
    """解析 HEX 文本为字节，支持 01 A2 3F / 0x01,0xA2 等写法"""
    tokens = re.findall(r"0?[0-9a-fA-F]{1,2}", text.replace("0x", "").replace("0X", ""))
    return bytes(int(t, 16) for t in tokens)


class TcpServerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("TCP Server 网络调试工具")
        self.root.geometry("900x680")
        self.root.minsize(640, 480)

        self.listen_sock = None
        self.running = False
        self.accept_thread = None
        self.rx_queue = queue.Queue()
        self.client_socks = {}  # "ip:port" -> socket
        self.lock = threading.Lock()
        self._listen_desc = ""

        self._build_ui()
        self.root.after(50, self._poll_rx_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- UI ----------------
    def _build_ui(self):
        # 顶部：参数配置
        top = ttk.LabelFrame(self.root, text="服务器设置")
        top.pack(fill="x", padx=8, pady=(8, 4))

        ttk.Label(top, text="绑定地址:").grid(row=0, column=0, padx=4, pady=6, sticky="e")
        self.ip_var = tk.StringVar(value="0.0.0.0")
        ttk.Entry(top, textvariable=self.ip_var, width=16).grid(row=0, column=1, padx=2)

        ttk.Label(top, text="端口:").grid(row=0, column=2, padx=4, sticky="e")
        self.port_var = tk.StringVar(value="9000")
        ttk.Entry(top, textvariable=self.port_var, width=8).grid(row=0, column=3, padx=2)

        self.btn_start = ttk.Button(top, text="启动", command=self._start)
        self.btn_start.grid(row=0, column=4, padx=8)
        self.btn_stop = ttk.Button(top, text="停止", command=self._stop, state="disabled")
        self.btn_stop.grid(row=0, column=5, padx=4)

        self.status_var = tk.StringVar(value="状态: 未启动")
        ttk.Label(top, textvariable=self.status_var).grid(row=0, column=6, padx=12)
        top.columnconfigure(6, weight=1)

        # 中部：接收区（尽可能大）—— 外层框架禁用 propagate 避免内容变化时引起整体抖动
        rx_frame = ttk.LabelFrame(self.root, text="接收区")
        rx_frame.pack(fill="both", expand=True, padx=8, pady=4)
        rx_frame.pack_propagate(False)

        opt_bar = ttk.Frame(rx_frame)
        opt_bar.pack(fill="x", padx=4, pady=2)

        ttk.Label(opt_bar, text="显示模式:").pack(side="left")
        self.rx_ascii_var = tk.BooleanVar(value=True)
        self.rx_hex_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_bar, text="ASCII", variable=self.rx_ascii_var,
                        command=lambda: self._on_mode_change("rx", "ascii")).pack(side="left", padx=2)
        ttk.Checkbutton(opt_bar, text="HEX", variable=self.rx_hex_var,
                        command=lambda: self._on_mode_change("rx", "hex")).pack(side="left", padx=2)

        self.btn_clear_rx = ttk.Button(opt_bar, text="清空接收区", command=self._clear_rx)
        self.btn_clear_rx.pack(side="right", padx=2)
        self.btn_save_rx = ttk.Button(opt_bar, text="保存日志", command=self._save_rx)
        self.btn_save_rx.pack(side="right", padx=2)

        # Text + 双滚动条放在独立容器，外层先横条(BOTTOM)再内容(TOP)，避免布局抖动
        rx_body = ttk.Frame(rx_frame)
        rx_body.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        rx_body.pack_propagate(False)

        # 横向滚动条 —— 放在底部
        rx_xscroll = ttk.Scrollbar(rx_body, orient="horizontal")
        rx_xscroll.pack(side="bottom", fill="x")

        # 竖滚条 + Text 容器
        rx_inner = ttk.Frame(rx_body)
        rx_inner.pack(side="top", fill="both", expand=True)

        self.rx_text = tk.Text(rx_inner, wrap="none", font=("Consolas", 11))
        self.rx_text.pack(side="left", fill="both", expand=True)
        rx_yscroll = ttk.Scrollbar(rx_inner, orient="vertical", command=self.rx_text.yview)
        rx_yscroll.pack(side="right", fill="y")
        self.rx_text.configure(yscrollcommand=rx_yscroll.set, xscrollcommand=rx_xscroll.set)
        rx_xscroll.configure(command=self.rx_text.xview)
        self.rx_text.configure(state="disabled")

        # 底部：发送区
        tx_frame = ttk.LabelFrame(self.root, text="发送区")
        tx_frame.pack(fill="x", padx=8, pady=(4, 8))

        tx_bar = ttk.Frame(tx_frame)
        tx_bar.pack(fill="x", padx=4, pady=2)

        ttk.Label(tx_bar, text="目标客户端:").pack(side="left")
        self.tx_addr_var = tk.StringVar(value="")
        ttk.Entry(tx_bar, textvariable=self.tx_addr_var, width=22).pack(side="left", padx=2)
        ttk.Button(tx_bar, text="最近客户端", command=self._fill_last_client).pack(side="left", padx=2)

        ttk.Label(tx_bar, text="发送模式:").pack(side="left", padx=(12, 0))
        self.tx_ascii_var = tk.BooleanVar(value=True)
        self.tx_hex_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(tx_bar, text="ASCII", variable=self.tx_ascii_var,
                        command=lambda: self._on_mode_change("tx", "ascii")).pack(side="left", padx=2)
        ttk.Checkbutton(tx_bar, text="HEX", variable=self.tx_hex_var,
                        command=lambda: self._on_mode_change("tx", "hex")).pack(side="left", padx=2)

        # 发送按钮放到这一行最右侧
        self.btn_send = ttk.Button(tx_bar, text="发送", command=self._send, width=10)
        self.btn_send.pack(side="right", padx=(0, 0))

        # 发送文本框 + 右侧竖滚条 + 底部横滚条，与接收区结构一致，左右 padding 对齐 (padx=4)
        tx_body = ttk.Frame(tx_frame)
        tx_body.pack(fill="x", padx=4, pady=(0, 4))

        # 先 pack 横滚条(底部)，再 pack 内容区(顶部)，避免布局抖动
        tx_xscroll = ttk.Scrollbar(tx_body, orient="horizontal")
        tx_xscroll.pack(side="bottom", fill="x")

        tx_inner = ttk.Frame(tx_body)
        tx_inner.pack(side="top", fill="both", expand=True)

        self.tx_text = tk.Text(tx_inner, height=4, wrap="none", font=("Consolas", 11))
        self.tx_text.pack(side="left", fill="both", expand=True)
        tx_yscroll = ttk.Scrollbar(tx_inner, orient="vertical", command=self.tx_text.yview)
        tx_yscroll.pack(side="right", fill="y")
        self.tx_text.configure(yscrollcommand=tx_yscroll.set, xscrollcommand=tx_xscroll.set)
        tx_xscroll.configure(command=self.tx_text.xview)

    def _on_mode_change(self, area: str, mode: str):
        """HEX/ASCII 两个勾选互斥"""
        if area == "rx":
            if mode == "hex":
                self.rx_ascii_var.set(not self.rx_hex_var.get())
            else:
                self.rx_hex_var.set(not self.rx_ascii_var.get())
        else:
            if mode == "hex":
                self.tx_ascii_var.set(not self.tx_hex_var.get())
            else:
                self.tx_hex_var.set(not self.tx_ascii_var.get())

    # ---------------- 服务器 ----------------
    def _start(self):
        try:
            ip = self.ip_var.get().strip() or "0.0.0.0"
            port = int(self.port_var.get().strip())
            if not (0 < port < 65536):
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "请输入有效的端口号 (1-65535)")
            return

        try:
            self.listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.listen_sock.bind((ip, port))
            self.listen_sock.listen(5)
            self.listen_sock.settimeout(0.5)
        except OSError as e:
            messagebox.showerror("错误", f"监听失败:\n{e}")
            if self.listen_sock:
                self.listen_sock.close()
                self.listen_sock = None
            return

        self.running = True
        self._listen_desc = f"{ip}:{port}"
        self.accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.accept_thread.start()

        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.status_var.set(f"状态: 监听中 {ip}:{port} (客户端:0)")
        self._append_system(f"服务器已启动，监听 {ip}:{port}\n")

    def _stop(self):
        self.running = False
        if self.listen_sock:
            try:
                self.listen_sock.close()
            except OSError:
                pass
            self.listen_sock = None
        with self.lock:
            for sock in self.client_socks.values():
                try:
                    sock.close()
                except OSError:
                    pass
            self.client_socks.clear()
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.status_var.set("状态: 未启动")
        self._append_system("服务器已停止\n")

    def _accept_loop(self):
        while self.running and self.listen_sock:
            try:
                sock, addr = self.listen_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            key = f"{addr[0]}:{addr[1]}"
            with self.lock:
                self.client_socks[key] = sock
            sock.settimeout(0.5)
            threading.Thread(target=self._recv_client, args=(sock, addr), daemon=True).start()
            self.rx_queue.put(("connect", key))

    def _recv_client(self, sock, addr):
        key = f"{addr[0]}:{addr[1]}"
        while self.running:
            try:
                data = sock.recv(MAX_RECV)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            self.rx_queue.put(("data", data, addr, datetime.now()))
        # 连接结束（对端关闭或异常）
        with self.lock:
            if self.client_socks.get(key) is sock:
                del self.client_socks[key]
        try:
            sock.close()
        except OSError:
            pass
        if self.running:
            self.rx_queue.put(("disconnect", key))

    def _poll_rx_queue(self):
        try:
            while True:
                item = self.rx_queue.get_nowait()
                if item[0] == "data":
                    self._display_rx(item[1], item[2], item[3])
                elif item[0] == "connect":
                    self._append_system(f"客户端已连接 {item[1]}\n")
                    # 无目标时自动选中新连接的客户端
                    if not self.tx_addr_var.get().strip():
                        self.tx_addr_var.set(item[1])
                elif item[0] == "disconnect":
                    self._append_system(f"客户端已断开 {item[1]}\n")
                    # 断开的正好是当前目标时清空
                    if self.tx_addr_var.get().strip() == item[1]:
                        self.tx_addr_var.set("")
                self.status_var.set(
                    f"状态: 监听中 {self._listen_desc} (客户端:{len(self.client_socks)})")
        except queue.Empty:
            pass
        self.root.after(50, self._poll_rx_queue)

    def _display_rx(self, data: bytes, addr, ts: datetime):
        # 目标客户端实时更新为最近一条接收数据的来源
        self.tx_addr_var.set(f"{addr[0]}:{addr[1]}")

        # 固定格式: [hhmmss-来源ip:port-字节数(6位)]
        head = ts.strftime("[%H%M%S-") + f"{addr[0]}:{addr[1]}-{len(data):06d}] "

        if self.rx_hex_var.get():
            body = bytes_to_hex(data)
        else:
            body = data.decode("utf-8", errors="replace")

        self.rx_text.configure(state="normal")
        self.rx_text.insert("end", head + body + "\n")
        self.rx_text.see("end")
        self.rx_text.configure(state="disabled")

    def _append_system(self, text: str):
        self.rx_text.configure(state="normal")
        self.rx_text.insert("end", f"--- {text}")
        self.rx_text.see("end")
        self.rx_text.configure(state="disabled")

    # ---------------- 发送 ----------------
    def _fill_last_client(self):
        with self.lock:
            keys = list(self.client_socks.keys())
        if keys:
            self.tx_addr_var.set(keys[-1])

    def _send(self):
        if not self.running or not self.listen_sock:
            messagebox.showwarning("提示", "请先启动服务器")
            return
        addr_text = self.tx_addr_var.get().strip()
        if not addr_text or ":" not in addr_text:
            messagebox.showerror("错误", "请填写目标客户端，格式: IP:端口")
            return

        with self.lock:
            sock = self.client_socks.get(addr_text)
        if sock is None:
            messagebox.showerror("错误", f"目标客户端 {addr_text} 未连接")
            return

        text = self.tx_text.get("1.0", "end").strip()
        if not text:
            return
        # 去掉回车/换行，仅发送可见字符
        text = text.replace("\r", "").replace("\n", "")
        if not text:
            return
        try:
            if self.tx_hex_var.get():
                data = parse_hex(text)
            else:
                data = text.encode("utf-8")
        except ValueError:
            messagebox.showerror("错误", "HEX 格式无效，请使用如 01 A2 3F 的格式")
            return
        if not data:
            messagebox.showerror("错误", "发送内容为空")
            return

        try:
            sock.sendall(data)
        except OSError as e:
            messagebox.showerror("错误", f"发送失败:\n{e}")
            return

    # ---------------- 其他 ----------------
    def _clear_rx(self):
        self.rx_text.configure(state="normal")
        self.rx_text.delete("1.0", "end")
        self.rx_text.configure(state="disabled")

    def _save_rx(self):
        from tkinter import filedialog
        content = self.rx_text.get("1.0", "end")
        if not content.strip():
            messagebox.showinfo("提示", "接收区为空")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".log", filetypes=[("日志文件", "*.log"), ("文本文件", "*.txt")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

    def _on_close(self):
        self._stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    TcpServerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
