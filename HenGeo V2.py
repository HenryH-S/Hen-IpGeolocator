from PIL import GimpGradientFile
import os
import sys
import socket
import ipaddress
import subprocess
import threading
import queue
import time
import requests
import webbrowser
import tkinter as tk
import customtkinter as ctk
from tkinter import ttk

# Windows subprocess flag to prevent CMD popup window
CREATE_NO_WINDOW = 0x08000000

# Attempt geolite2 import, fallback gracefully to API if unavailable/outdated
GEOLITE2_AVAILABLE = False
try:
    from geolite2 import geolite2
    geolite2_reader = geolite2.reader()
    GEOLITE2_AVAILABLE = True
except Exception:
    geolite2_reader = None

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# Look for bundled tshark first (in _tshark subfolder next to exe), then system Wireshark
_APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
_BUNDLED_TSHARK = os.path.join(_APP_DIR, "_tshark", "tshark.exe")
TSHARK_PATH = _BUNDLED_TSHARK if os.path.exists(_BUNDLED_TSHARK) else r"C:\Program Files\Wireshark\tshark.exe"

# Universal App & Protocol Presets optimized for all services (OmeTV, WhatsApp, Discord, Zoom, etc.)
APP_PRESETS = {
    "🌐 Universal IP Tracker (All Apps)": "",
    "🎯 OmeTV / WebRTC Peer (Direct Video)": "udp and not port 53 and not port 443 and not port 80",
    "📞 WhatsApp Calls (Audio/Video P2P)": "udp port 3478 or udp portrange 50000-60000",
    "🎧 Discord (Voice / Video Channels)": "udp portrange 50000-65000 or udp port 3478",
    "📹 Zoom / Teams / Skype": "udp portrange 8801-8802 or udp port 3478",
    "⚡ STUN / TURN Discovery": "udp port 3478 or udp port 19302 or udp port 3479 or udp port 3480",
    "📡 All UDP Traffic (Voice / Video / P2P)": "udp",
    "💻 All TCP Traffic (Web / Data)": "tcp"
}

def is_private_ip(ip_str):
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private or ip.is_loopback or ip.is_multicast or ip.is_reserved
    except ValueError:
        return True

class GeoIPResolver:
    def __init__(self):
        self.cache = {}

    def resolve(self, ip):
        if ip in self.cache:
            return self.cache[ip]

        if is_private_ip(ip):
            res = ("Local Network", "Private IP", "Local", "🏠")
            self.cache[ip] = res
            return res

        # 1. Try GeoLite2 first if functional
        if GEOLITE2_AVAILABLE and geolite2_reader:
            try:
                data = geolite2_reader.get(ip)
                if data:
                    country = data.get("country", {}).get("names", {}).get("en", "Unknown")
                    subdivision = "Unknown"
                    if "subdivisions" in data and len(data["subdivisions"]) > 0:
                        subdivision = data["subdivisions"][0].get("names", {}).get("en", "Unknown")
                    city = data.get("city", {}).get("names", {}).get("en", "Unknown")
                    res = (country, subdivision, city, "🌍")
                    self.cache[ip] = res
                    return res
            except Exception:
                pass

        # 2. Online API Fallback (ip-api.com)
        try:
            url = f"http://ip-api.com/json/{ip}?fields=status,country,regionName,city"
            resp = requests.get(url, timeout=3).json()
            if resp.get("status") == "success":
                country = resp.get("country", "Unknown")
                subdivision = resp.get("regionName", "Unknown")
                city = resp.get("city", "Unknown")
                res = (country, subdivision, city, "🌐")
                self.cache[ip] = res
                return res
        except Exception:
            pass

        res = ("Unknown", "Unknown", "Unknown", "❓")
        self.cache[ip] = res
        return res

class IPOMGApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Hen Geolocator - Universal IP Tracker")
        self.geometry("1200x750")
        self.minsize(950, 600)

        self.resolver = GeoIPResolver()
        self.capture_process = None
        self.is_capturing = False
        self.packet_queue = queue.Queue()

        self.setup_ui()
        self.load_interfaces()
        self.after(100, self.process_queue)

    def get_local_ips(self):
        """Returns set of all local IPv4 addresses on this machine."""
        local_ips = {"127.0.0.1"}
        try:
            hostname = socket.gethostname()
            for ip in socket.gethostbyname_ex(hostname)[2]:
                local_ips.add(ip)
        except Exception:
            pass
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ips.add(s.getsockname()[0])
            s.close()
        except Exception:
            pass
        return local_ips

    def setup_ui(self):
        self.local_ips = self.get_local_ips()

        # Header / Controls Frame
        self.top_frame = ctk.CTkFrame(self, corner_radius=10)
        self.top_frame.pack(fill="x", padx=15, pady=12)

        # Title & Local IP
        self.title_label = ctk.CTkLabel(
            self.top_frame, 
            text="📡 Hen Geolocator - Universal IP Tracker", 
            font=ctk.CTkFont(size=20, weight="bold")
        )
        self.title_label.pack(side="left", padx=15, pady=10)

        primary_ip = next(iter([ip for ip in self.local_ips if ip != "127.0.0.1"]), "127.0.0.1")
        self.local_ip_label = ctk.CTkLabel(
            self.top_frame,
            text=f"My Local IP: {primary_ip}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#3B8ED0"
        )
        self.local_ip_label.pack(side="left", padx=10)

        # Start / Stop Button
        self.btn_toggle = ctk.CTkButton(
            self.top_frame, 
            text="▶ Start Capture", 
            fg_color="#2FA572", 
            hover_color="#1E7B52",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self.toggle_capture
        )
        self.btn_toggle.pack(side="right", padx=15, pady=10)

        # Selectors Frame
        self.controls_frame = ctk.CTkFrame(self, corner_radius=8)
        self.controls_frame.pack(fill="x", padx=15, pady=(0, 10))

        # Interface Selector
        self.iface_label = ctk.CTkLabel(self.controls_frame, text="Interface:", font=ctk.CTkFont(weight="bold"))
        self.iface_label.pack(side="left", padx=(15, 5), pady=8)

        self.iface_dropdown = ctk.CTkOptionMenu(self.controls_frame, values=["Loading Interfaces..."], width=320)
        self.iface_dropdown.pack(side="left", padx=5, pady=8)

        # App Preset Selector
        self.preset_label = ctk.CTkLabel(self.controls_frame, text="Target Preset:", font=ctk.CTkFont(weight="bold"))
        self.preset_label.pack(side="left", padx=(15, 5), pady=8)

        self.preset_dropdown = ctk.CTkOptionMenu(
            self.controls_frame, 
            values=list(APP_PRESETS.keys()),
            width=280
        )
        self.preset_dropdown.pack(side="left", padx=5, pady=8)

        # Clear Logs Button
        self.btn_clear = ctk.CTkButton(
            self.controls_frame,
            text="🗑 Clear Logs",
            fg_color="#4A4D50",
            hover_color="#3A3C3E",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=110,
            command=self.clear_logs
        )
        self.btn_clear.pack(side="right", padx=15, pady=8)

        # Highlight Banner Frame
        self.peer_frame = ctk.CTkFrame(self, fg_color="#1F2A38", corner_radius=8)
        self.peer_frame.pack(fill="x", padx=15, pady=(0, 10))

        self.peer_title = ctk.CTkLabel(
            self.peer_frame,
            text="🎯 LATEST TARGET IP:",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#E74C3C"
        )
        self.peer_title.pack(side="left", padx=15, pady=8)

        self.peer_ip_label = ctk.CTkLabel(
            self.peer_frame,
            text="Waiting for active connections...",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#2ECC71"
        )
        self.peer_ip_label.pack(side="left", padx=10, pady=8)

        # Status & Stats Bar
        self.status_frame = ctk.CTkFrame(self, height=35, corner_radius=6)
        self.status_frame.pack(fill="x", padx=15, pady=(0, 10))

        self.status_label = ctk.CTkLabel(self.status_frame, text="Status: Ready", font=ctk.CTkFont(size=12))
        self.status_label.pack(side="left", padx=15)

        self.count_label = ctk.CTkLabel(self.status_frame, text="Packets Captured: 0 | Unique Remote IPs: 0", font=ctk.CTkFont(size=12))
        self.count_label.pack(side="right", padx=15)

        self.total_packets = 0
        self.unique_ips = set()

        # Treeview Styling
        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "Treeview",
            background="#2a2d2e",
            foreground="white",
            fieldbackground="#2a2d2e",
            rowheight=28,
            font=("Segoe UI", 10)
        )
        style.configure("Treeview.Heading", background="#1f2122", foreground="white", font=("Segoe UI", 10, "bold"))
        style.map("Treeview", background=[("selected", "#1f538d")])

        # Table Frame
        self.table_frame = ctk.CTkFrame(self, corner_radius=10)
        self.table_frame.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        columns = ("time", "src", "dst", "proto", "country", "subdivision", "city")
        self.tree = ttk.Treeview(self.table_frame, columns=columns, show="headings", selectmode="browse")

        self.tree.heading("time", text="Time")
        self.tree.heading("src", text="Source IP")
        self.tree.heading("dst", text="Destination IP")
        self.tree.heading("proto", text="Protocol")
        self.tree.heading("country", text="Country")
        self.tree.heading("subdivision", text="State / Region")
        self.tree.heading("city", text="City")

        self.tree.column("time", width=90, anchor="center")
        self.tree.column("src", width=140, anchor="w")
        self.tree.column("dst", width=140, anchor="w")
        self.tree.column("proto", width=100, anchor="center")
        self.tree.column("country", width=160, anchor="w")
        self.tree.column("subdivision", width=160, anchor="w")
        self.tree.column("city", width=160, anchor="w")

        scrollbar = ttk.Scrollbar(self.table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        scrollbar.pack(side="right", fill="y", padx=(0, 10), pady=10)

        # Right-click context menu and copy bindings
        self.tree.bind("<Button-3>", self.show_context_menu)
        self.tree.bind("<Button-2>", self.show_context_menu)  # macOS right-click support
        self.tree.bind("<Control-c>", self.copy_selection_shortcut)

    def copy_to_clipboard(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        self.status_label.configure(text=f"Status: Copied '{text}' to clipboard!")

    def copy_selection_shortcut(self, event=None):
        selected_items = self.tree.selection()
        if selected_items:
            row_texts = []
            for item in selected_items:
                values = self.tree.item(item, "values")
                if values:
                    row_texts.append(" | ".join(values))
            if row_texts:
                self.copy_to_clipboard("\n".join(row_texts))

    def show_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            values = self.tree.item(item, "values")
            if values:
                timestamp, src_ip, dst_ip, proto, country, subdivision, city = values
                
                # Determine remote/target IP
                target_ip = dst_ip if src_ip in self.local_ips or is_private_ip(src_ip) else src_ip
                if is_private_ip(target_ip):
                    target_ip = src_ip if not is_private_ip(src_ip) else dst_ip

                context_menu = tk.Menu(
                    self, 
                    tearoff=0, 
                    bg="#2a2d2e", 
                    fg="white", 
                    activebackground="#1f538d", 
                    activeforeground="white",
                    font=("Segoe UI", 9)
                )
                context_menu.add_command(
                    label=f"📋 Copy Source IP ({src_ip})", 
                    command=lambda: self.copy_to_clipboard(src_ip)
                )
                context_menu.add_command(
                    label=f"📋 Copy Destination IP ({dst_ip})", 
                    command=lambda: self.copy_to_clipboard(dst_ip)
                )
                context_menu.add_command(
                    label=f"🎯 Copy Target IP ({target_ip})", 
                    command=lambda: self.copy_to_clipboard(target_ip)
                )
                context_menu.add_separator()
                context_menu.add_command(
                    label=f"🌐 Open {target_ip} in whatismyipaddress.com", 
                    command=lambda: webbrowser.open(f"https://whatismyipaddress.com/ip/{target_ip}")
                )
                context_menu.add_separator()
                context_menu.add_command(
                    label="📄 Copy Entire Row", 
                    command=lambda: self.copy_to_clipboard(" | ".join(values))
                )
                
                try:
                    context_menu.tk_popup(event.x_root, event.y_root)
                finally:
                    context_menu.grab_release()

    def clear_logs(self):
        """Clears all table rows, counters, and target IP banner."""
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.total_packets = 0
        self.unique_ips.clear()
        self.count_label.configure(text="Packets Captured: 0 | Unique Remote IPs: 0")
        self.peer_ip_label.configure(text="Waiting for active connections...")

    def load_interfaces(self):
        def _get_ifaces():
            tshark_bin = TSHARK_PATH if os.path.exists(TSHARK_PATH) else "tshark"
            try:
                out = subprocess.check_output(
                    [tshark_bin, "-D"], 
                    stderr=subprocess.STDOUT, 
                    encoding="utf-8", 
                    errors="replace",
                    creationflags=CREATE_NO_WINDOW
                )
                lines = [line.strip() for line in out.strip().split("\n") if line.strip()]
                return lines
            except Exception:
                return []

        def _update(lines):
            if lines:
                display_options = []
                self.iface_map = {}
                default_choice = None

                for line in lines:
                    parts = line.split(" ", 1)
                    num = parts[0].rstrip(".")
                    rest = parts[1] if len(parts) > 1 else line

                    dev_name = rest.split(" ")[0].strip()
                    friendly_name = rest[len(dev_name):].strip()
                    if friendly_name.startswith("(") and friendly_name.endswith(")"):
                        friendly_name = friendly_name[1:-1].strip()

                    label = f"{num}. {friendly_name}" if friendly_name else line
                    display_options.append(label)
                    self.iface_map[label] = dev_name if dev_name.startswith("\\Device\\") else num

                    lower_label = label.lower()
                    if not default_choice and any(k in lower_label for k in ["nordlynx", "wi-fi", "wifi", "ethernet"]):
                        default_choice = label

                if not default_choice and display_options:
                    default_choice = display_options[0]

                self.iface_dropdown.configure(values=display_options)
                if default_choice:
                    self.iface_dropdown.set(default_choice)
            else:
                self.iface_dropdown.configure(values=["No interfaces found"])

        threading.Thread(target=lambda: self.after(0, lambda: _update(_get_ifaces())), daemon=True).start()

    def toggle_capture(self):
        if self.is_capturing:
            self.stop_capture()
        else:
            self.start_capture()

    def start_capture(self):
        tshark_bin = TSHARK_PATH if os.path.exists(TSHARK_PATH) else "tshark"
        
        selected_display = self.iface_dropdown.get()
        iface_target = self.iface_map.get(selected_display, "")

        if not iface_target and selected_display and not selected_display.startswith("Loading") and not selected_display.startswith("No interfaces"):
            iface_target = selected_display.split(".")[0].strip()

        cmd = [tshark_bin, "-l", "-n"]
        if iface_target:
            cmd.extend(["-i", iface_target])

        selected_preset = self.preset_dropdown.get()
        filter_expr = APP_PRESETS.get(selected_preset, "")
        if filter_expr:
            cmd.extend(["-f", filter_expr])

        try:
            self.capture_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW
            )
        except Exception as e:
            self.status_label.configure(text=f"Status: Failed to start TShark ({e})")
            return

        self.is_capturing = True
        self.btn_toggle.configure(text="⏹ Stop Capture", fg_color="#C0392B", hover_color="#962D22")
        self.status_label.configure(text=f"Status: Capturing on {selected_display} ({selected_preset})...")

        threading.Thread(target=self.read_tshark_output, daemon=True).start()

    def stop_capture(self):
        self.is_capturing = False
        if self.capture_process:
            try:
                self.capture_process.terminate()
            except Exception:
                pass
            self.capture_process = None

        self.btn_toggle.configure(text="▶ Start Capture", fg_color="#2FA572", hover_color="#1E7B52")
        self.status_label.configure(text="Status: Stopped")

    def read_tshark_output(self):
        if not self.capture_process:
            return

        for line in iter(self.capture_process.stdout.readline, ''):
            if not self.is_capturing:
                break
            if line and not line.startswith("Capturing on"):
                self.packet_queue.put(line)

    def process_queue(self):
        processed_count = 0
        while not self.packet_queue.empty() and processed_count < 30:
            line = self.packet_queue.get()
            self.parse_and_add_line(line)
            processed_count += 1

        self.after(100, self.process_queue)

    def parse_and_add_line(self, line):
        clean_line = line.strip()
        if not clean_line or clean_line.startswith("Capturing") or clean_line.startswith("tshark:"):
            return

        parts = clean_line.split()
        if len(parts) < 4:
            return

        timestamp = time.strftime("%H:%M:%S")
        src_ip = None
        dst_ip = None

        if "->" in parts:
            idx = parts.index("->")
            if idx > 0 and idx < len(parts) - 1:
                src_ip = parts[idx - 1]
                dst_ip = parts[idx + 1]
        elif "→" in parts:
            idx = parts.index("→")
            if idx > 0 and idx < len(parts) - 1:
                src_ip = parts[idx - 1]
                dst_ip = parts[idx + 1]
        else:
            ips = [p for p in parts if p.count(".") == 3 and not p.endswith(":")]
            if len(ips) >= 2:
                src_ip, dst_ip = ips[0], ips[1]

        if not src_ip or not dst_ip:
            return

        # Determine target IP (must not be any of our local IPs)
        target_ip = None
        if src_ip in self.local_ips or is_private_ip(src_ip):
            target_ip = dst_ip
        else:
            target_ip = src_ip

        if target_ip in self.local_ips or is_private_ip(target_ip):
            return

        self.unique_ips.add(target_ip)
        country, subdivision, city, icon = self.resolver.resolve(target_ip)

        # Highlight target IP in top banner
        self.peer_ip_label.configure(
            text=f"{target_ip}  ({icon} {country}, {subdivision}, {city})"
        )

        # Proto extraction
        proto = "UDP/TCP"
        for p in ["STUN", "UDP", "TCP", "DNS", "TLS", "HTTP", "TLSv1.2", "TLSv1.3"]:
            if p in parts:
                proto = p
                break

        self.total_packets += 1
        self.count_label.configure(
            text=f"Packets Captured: {self.total_packets} | Unique Remote IPs: {len(self.unique_ips)}"
        )

        self.tree.insert(
            "", 
            0, 
            values=(timestamp, src_ip, dst_ip, proto, f"{icon} {country}", subdivision, city)
        )

        children = self.tree.get_children()
        if len(children) > 500:
            self.tree.delete(children[-1])

    def on_closing(self):
        self.stop_capture()
        self.destroy()

if __name__ == "__main__":
    app = IPOMGApp()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
