# -*- coding: utf-8 -*-
"""TCP Client 网络调试工具
基于 tkinter，支持 HEX/ASCII 显示与发送模式（勾选选择）。
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


class TcpClientApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("TCP Client 网络调试工具")
        self.root.geometry("900x680")
        self.root.minsize(640, 480)

        self.sock = None
        self.running = False
        self.rx_thread = None
        self.rx_queue = queue.Queue()
        self.peer_addr = ("", 0)

        self._build_ui()
        self.root.after(50, self._poll_rx_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- UI ----------------
    def _build_ui(self):
        # 顶部：参数配置
        top = ttk.LabelFrame(self.root, text="客户端设置")
        top.pack(fill="x", padx=8, pady=(8, 4))

        ttk.Label(top, text="服务器地址:").grid(row=0, column=0, padx=4, pady=6, sticky="e")
        self.server_var = tk.StringVar(value="127.0.0.1:9000")
        ttk.Entry(top, textvariable=self.server_var, width=22).grid(row=0, column=1, padx=2)

        ttk.Label(top, text="本地地址(0随机):").grid(row=0, column=2, padx=4, sticky="e")
        # 支持 IP:端口（指定绑定网卡，如 192.168.1.16:9000）；仅填端口或 0 则 IP 默认 0.0.0.0
        self.local_addr_var = tk.StringVar(value="0.0.0.0:0")
        ttk.Entry(top, textvariable=self.local_addr_var, width=18).grid(row=0, column=3, padx=2)

        self.btn_connect = ttk.Button(top, text="连接", command=self._connect)
        self.btn_connect.grid(row=0, column=4, padx=8)
        self.btn_disconnect = ttk.Button(top, text="断开", command=self._disconnect, state="disabled")
        self.btn_disconnect.grid(row=0, column=5, padx=4)

        self.status_var = tk.StringVar(value="状态: 未连接")
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

        ttk.Label(tx_bar, text="发送模式:").pack(side="left")
        self.tx_ascii_var = tk.BooleanVar(value=True)
        self.tx_hex_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(tx_bar, text="ASCII", variable=self.tx_ascii_var,
                        command=lambda: self._on_mode_change("tx", "ascii")).pack(side="left", padx=2)
        ttk.Checkbutton(tx_bar, text="HEX", variable=self.tx_hex_var,
                        command=lambda: self._on_mode_change("tx", "hex")).pack(side="left", padx=2)

        self.auto_reply = tk.BooleanVar(value=False)
        ttk.Checkbutton(tx_bar, text="收到数据自动回复相同内容", variable=self.auto_reply).pack(side="left", padx=12)

        # 发送尾部追加符：\r(0x0D) / \n(0x0A)
        self.append_cr_var = tk.BooleanVar(value=False)
        self.append_lf_var = tk.BooleanVar(value=False)
        ttk.Label(tx_bar, text="追加:").pack(side="left", padx=(12, 2))
        ttk.Checkbutton(tx_bar, text="\\r(0x0D)", variable=self.append_cr_var).pack(side="left", padx=2)
        ttk.Checkbutton(tx_bar, text="\\n(0x0A)", variable=self.append_lf_var).pack(side="left", padx=2)

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

    # ---------------- socket ----------------
    def _connect(self):
        server_text = self.server_var.get().strip()
        if not server_text or ":" not in server_text:
            messagebox.showerror("错误", "请填写服务器地址，格式: IP:端口")
            return
        try:
            ip, port = server_text.rsplit(":", 1)
            port = int(port)
        except ValueError:
            messagebox.showerror("错误", "服务器地址格式错误，应为 IP:端口")
            return

        # 解析本地绑定地址: 支持 "0"/"9000"（仅端口，网卡 IP 取 0.0.0.0）
        # 与 "0.0.0.0:0"/"192.168.1.16:9000"（指定网卡 IP + 端口）
        local_text = self.local_addr_var.get().strip()
        local_ip = "0.0.0.0"
        try:
            if ":" in local_text:
                ip_part, port_part = local_text.rsplit(":", 1)
                local_ip = ip_part.strip() or "0.0.0.0"
                local_port = int(port_part.strip() or "0")
            else:
                local_port = int(local_text or "0")
            if not (0 <= local_port < 65536):
                raise ValueError
            socket.inet_aton(local_ip)
        except (ValueError, OSError):
            messagebox.showerror(
                "错误", "本地地址无效，格式: IP:端口，如 192.168.1.16:9000\n（端口 0 表示随机；IP 留空/0.0.0.0 表示不限网卡）")
            return

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if local_port or local_ip != "0.0.0.0":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((local_ip, local_port))
            sock.settimeout(3.0)
            sock.connect((ip, port))
            sock.settimeout(0.5)
        except OSError as e:
            messagebox.showerror("错误", f"连接失败:\n{e}")
            try:
                sock.close()
            except OSError:
                pass
            return

        self.sock = sock
        self.peer_addr = (ip, port)
        self.running = True
        self.rx_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self.rx_thread.start()

        self.btn_connect.configure(state="disabled")
        self.btn_disconnect.configure(state="normal")
        l_ip, l_port = sock.getsockname()
        self.status_var.set(f"状态: 已连接 {ip}:{port} (本地:{l_ip}:{l_port})")
        self._append_system(f"已连接服务器 {ip}:{port} (本地:{l_ip}:{l_port})\n")

    def _disconnect(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        self._set_disconnected_ui()
        self._append_system("已主动断开连接\n")

    def _set_disconnected_ui(self):
        self.btn_connect.configure(state="normal")
        self.btn_disconnect.configure(state="disabled")
        self.status_var.set("状态: 未连接")

    def _recv_loop(self):
        while self.running and self.sock:
            try:
                data = self.sock.recv(MAX_RECV)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            self.rx_queue.put(("data", data, self.peer_addr, datetime.now()))
        # 非主动断开（对端关闭或异常）→ 通知 UI
        if self.running:
            self.running = False
            try:
                if self.sock:
                    self.sock.close()
            except OSError:
                pass
            self.sock = None
            self.rx_queue.put(("closed",))

    def _poll_rx_queue(self):
        try:
            while True:
                item = self.rx_queue.get_nowait()
                if item[0] == "data":
                    self._display_rx(item[1], item[2], item[3])
                elif item[0] == "closed":
                    self._set_disconnected_ui()
                    self._append_system("与服务器的连接已断开\n")
        except queue.Empty:
            pass
        self.root.after(50, self._poll_rx_queue)

    def _display_rx(self, data: bytes, addr, ts: datetime):
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

        if self.auto_reply.get():
            self._send_data(data)

    def _append_system(self, text: str):
        self.rx_text.configure(state="normal")
        self.rx_text.insert("end", f"--- {text}")
        self.rx_text.see("end")
        self.rx_text.configure(state="disabled")

    # ---------------- 发送 ----------------
    def _send(self):
        if not self.running or not self.sock:
            messagebox.showwarning("提示", "请先连接服务器")
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
        # 按勾选在尾部追加 \r(0x0D) / \n(0x0A)
        if self.append_cr_var.get():
            data += b"\r"
        if self.append_lf_var.get():
            data += b"\n"
        self._send_data(data)

    def _send_data(self, data: bytes):
        if not self.running or not self.sock:
            messagebox.showwarning("提示", "请先连接服务器")
            return
        try:
            self.sock.sendall(data)
        except OSError as e:
            messagebox.showerror("错误", f"发送失败:\n{e}")

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
        self._disconnect()
        self.root.destroy()


def main():
    root = tk.Tk()
    TcpClientApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
