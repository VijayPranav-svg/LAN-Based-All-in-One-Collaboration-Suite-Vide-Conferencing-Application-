import socket
import threading
import struct
import time
import json
import base64
import math
from enum import Enum
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import ImageGrab, Image, ImageTk
import cv2
import pyaudio
import numpy as np
import io
import sys
import os
import platform

# Suppress OpenCV warnings for cleaner output (optional)
# Uncomment the next two lines to hide camera detection warnings:
# os.environ['OPENCV_LOG_LEVEL'] = 'ERROR'
# cv2.setLogLevel(0)

# Detect OS for camera backend
IS_WINDOWS = platform.system() == 'Windows'
IS_LINUX = platform.system() == 'Linux'

# Virtual camera fallback
try:
    from virtual_camera import VirtualCamera
    HAS_VIRTUAL_CAMERA = True
except ImportError:
    HAS_VIRTUAL_CAMERA = False

def list_audio_devices(p):
    """Print available audio devices for debugging/selection."""
    try:
        info = p.get_host_api_info_by_index(0)
        numdevices = info.get('deviceCount', 0)
        print("Audio Devices:")
        for i in range(0, numdevices):
            dev = p.get_device_info_by_host_api_device_index(0, i)
            name = dev.get('name')
            in_ch = dev.get('maxInputChannels')
            out_ch = dev.get('maxOutputChannels')
            print(f"  {i}: {name} (in={in_ch}, out={out_ch})")
    except Exception as e:
        print(f"Failed to list audio devices: {e}")

def get_default_output_device_index(p):
    """Return default output device index if available, else None."""
    try:
        return p.get_default_output_device_info()['index']
    except Exception:
        return None

class MessageType(Enum):
    CHAT = 1
    FILE_NOTIFY = 2
    FILE_REQUEST = 3
    FILE_CHUNK = 4
    SCREEN_START = 5
    SCREEN_IMAGE = 6
    SCREEN_STOP = 7
    USER_JOIN = 8
    USER_LEAVE = 9
    VIDEO_STREAM = 10
    AUDIO_STREAM = 11
    UDP_REGISTER = 12

TCP_PORT = 5000
UDP_PORT = 5001
BUFFER_SIZE = 65536
CHUNK_SIZE = 1024

class Client:
    def __init__(self, server_ip, username):
        self.username = username
        self.server_ip = server_ip
        self.running = True
        
        # TCP connection
        self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp_socket.connect((server_ip, TCP_PORT))
        self.tcp_socket.send(username.encode())
        
        # Get confirmed username (in case of duplicates)
        self.username = self.tcp_socket.recv(1024).decode()
        
        # UDP socket
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_socket.bind(('', 0))  # Bind to any available port
        udp_port = self.udp_socket.getsockname()[1]
        
        # Register UDP port with server
        self.send_tcp(MessageType.UDP_REGISTER, {'port': udp_port})
        print(f"Connected as {self.username}, UDP port: {udp_port}")
        
        # Video/Audio state
        self.cap = None
        self.audio = None
        self.input_stream = None
        self.output_stream = None
        self.is_sharing = False
        
        # Create GUI FIRST before starting network threads
        self.gui = GUI(self)
        
        # Now start network threads (after GUI exists)
        threading.Thread(target=self.handle_tcp_messages, daemon=True).start()
        threading.Thread(target=self.handle_udp_receives, daemon=True).start()
        
        # Set close handler and start GUI loop
        self.gui.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.gui.root.mainloop()

    def on_closing(self):
        self.running = False
        self.cleanup()
        self.gui.root.destroy()

    def cleanup(self):
        try:
            if self.cap:
                self.cap.release()
            if self.input_stream:
                self.input_stream.stop_stream()
                self.input_stream.close()
            if self.output_stream:
                self.output_stream.stop_stream()
                self.output_stream.close()
            if self.audio:
                self.audio.terminate()
            self.tcp_socket.close()
            self.udp_socket.close()
        except:
            pass

    def handle_tcp_messages(self):
        buffer = b''
        while self.running:
            try:
                data = self.tcp_socket.recv(BUFFER_SIZE)
                if not data:
                    break
                
                buffer += data
                
                # Process complete messages
                while len(buffer) >= 5:
                    msg_type_byte = buffer[0]
                    length = struct.unpack('!I', buffer[1:5])[0]
                    
                    if len(buffer) < 5 + length:
                        break
                    
                    msg_type = MessageType(msg_type_byte)
                    payload_bytes = buffer[5:5+length]
                    buffer = buffer[5+length:]
                    
                    try:
                        payload = json.loads(payload_bytes.decode())
                        # Use after() to run GUI updates on main thread
                        if hasattr(self, 'gui') and self.gui:
                            self.gui.root.after(0, self.gui.handle_message, msg_type, payload)
                    except Exception as e:
                        print(f"Error processing TCP message: {e}")
                        
            except Exception as e:
                if self.running:
                    print(f"TCP receive error: {e}")
                break

    def handle_udp_receives(self):
        while self.running:
            try:
                data, addr = self.udp_socket.recvfrom(BUFFER_SIZE)
                
                if len(data) < 5:
                    continue
                
                msg_type = MessageType(data[0])
                length = struct.unpack('!I', data[1:5])[0]
                full_payload = data[5:5+length]
                
                # Extract username from payload
                username_len = struct.unpack('!I', full_payload[:4])[0]
                sender_username = full_payload[4:4+username_len].decode()
                payload = full_payload[4+username_len:]
                
                if msg_type == MessageType.VIDEO_STREAM:
                    if hasattr(self, 'gui') and self.gui:
                        self.gui.root.after(0, self.gui.update_video, payload, sender_username)
                elif msg_type == MessageType.AUDIO_STREAM:
                    # Play audio directly (not on GUI thread)
                    self.play_audio(payload)
                    # Update GUI to show who's speaking
                    if hasattr(self, 'gui') and self.gui:
                        self.gui.root.after(0, self.gui.update_speaker, sender_username)
                    
            except Exception as e:
                if self.running:
                    print(f"UDP receive error: {e}")

    def send_tcp(self, msg_type, payload):
        try:
            data = self.pack_message(msg_type, payload)
            self.tcp_socket.send(data)
        except Exception as e:
            print(f"TCP send error: {e}")

    def send_udp(self, msg_type, payload):
        try:
            data = self.pack_udp_message(msg_type, payload)
            self.udp_socket.sendto(data, (self.server_ip, UDP_PORT))
        except Exception as e:
            print(f"UDP send error: {e}")

    def pack_message(self, msg_type, payload):
        payload_bytes = json.dumps(payload).encode()
        length = len(payload_bytes)
        return struct.pack('!BI', msg_type.value, length) + payload_bytes

    def pack_udp_message(self, msg_type, payload):
        # For UDP, payload is raw bytes (video/audio data)
        length = len(payload)
        return struct.pack('!BI', msg_type.value, length) + payload

    def start_video_stream(self, device_index=None):
        if self.cap is None:
            try:
                # Use appropriate backend for the OS
                cam_index = 0 if device_index is None else device_index
                
                if IS_WINDOWS:
                    # DirectShow for Windows
                    self.cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
                elif IS_LINUX:
                    # V4L2 for Linux
                    self.cap = cv2.VideoCapture(cam_index, cv2.CAP_V4L2)
                else:
                    # Default backend for macOS and others
                    self.cap = cv2.VideoCapture(cam_index)
                
                if not self.cap.isOpened():
                    # Fallback to default backend
                    self.cap = cv2.VideoCapture(cam_index)
                
                if not self.cap.isOpened():
                    # Try virtual camera as last resort
                    if HAS_VIRTUAL_CAMERA:
                        print("No physical camera detected, using virtual camera")
                        self.cap = VirtualCamera(cam_index)
                        if hasattr(self, 'gui'):
                            self.gui.root.after(0, messagebox.showinfo, "Virtual Camera", 
                                "No physical camera detected. Using virtual test pattern.\n" +
                                "To use a real camera, connect one and restart the app.")
                    else:
                        print("Warning: Could not open camera")
                        self.cap = None
                        if hasattr(self, 'gui'):
                            messagebox.showwarning("Camera Error", 
                                "Could not access camera. Please check:\n" +
                                "1. Camera is connected\n" +
                                "2. No other app is using it\n" +
                                "3. Camera permissions are granted")
                        return
                
                if not self.cap or not self.cap.isOpened():
                    print("Warning: Could not initialize any camera")
                    self.cap = None
                    return
                
                # Test reading a frame to make sure camera actually works
                ret, test_frame = self.cap.read()
                if not ret or test_frame is None:
                    print("Warning: Camera opened but cannot read frames")
                    self.cap.release()
                    self.cap = None
                    if hasattr(self, 'gui'):
                        messagebox.showwarning("Camera Error", 
                            "Camera opened but cannot capture frames.\n" +
                            "Try:\n" +
                            "1. Closing other apps using the camera\n" +
                            "2. Restarting your computer\n" +
                            "3. Updating camera drivers")
                    return
                
                # Set camera properties for better compatibility (skip for virtual camera)
                if not isinstance(self.cap, VirtualCamera if HAS_VIRTUAL_CAMERA else type(None)):
                    self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    self.cap.set(cv2.CAP_PROP_FPS, 15)
                
                print("Camera initialized successfully")
                threading.Thread(target=self.video_stream_loop, daemon=True).start()
                
            except Exception as e:
                print(f"Camera init error: {e}")
                if self.cap:
                    self.cap.release()
                self.cap = None
                if hasattr(self, 'gui'):
                    messagebox.showerror("Camera Error", f"Failed to initialize camera: {e}")

    def video_stream_loop(self):
        consecutive_failures = 0
        max_failures = 10  # Stop after 10 consecutive failures
        frame_count = 0
        
        while self.running and self.cap and self.cap.isOpened():
            try:
                ret, frame = self.cap.read()
                
                if not ret or frame is None:
                    consecutive_failures += 1
                    if consecutive_failures >= max_failures:
                        print("Too many camera read failures, stopping video stream")
                        if hasattr(self, 'gui') and self.gui:
                            self.gui.root.after(0, messagebox.showwarning, "Camera Error", 
                                "Camera stopped working. Please restart the application.")
                        break
                    time.sleep(0.1)
                    continue
                
                # Reset failure counter on success
                consecutive_failures = 0
                frame_count += 1
                
                # Compress to JPEG
                frame = cv2.resize(frame, (320, 240))
                _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                payload = buffer.tobytes()
                self.send_udp(MessageType.VIDEO_STREAM, payload)
                
                # Debug output every 30 frames (~2-3 seconds)
                if frame_count % 30 == 0:
                    print(f"Sent {frame_count} video frames ({len(payload)} bytes)")
                
                time.sleep(1/12)  # ~12 FPS for stability
                
            except Exception as e:
                consecutive_failures += 1
                if consecutive_failures >= max_failures:
                    print(f"Video stream error: {e}")
                    break
                time.sleep(0.1)
        
        # Clean up
        if self.cap:
            self.cap.release()
            self.cap = None
            print("Video stream stopped")

    def stop_video_stream(self):
        if self.cap:
            self.cap.release()
            self.cap = None

    def start_audio_stream(self):
        if self.audio is None:
            self.audio = pyaudio.PyAudio()
        
        if self.input_stream is None:
            try:
                self.input_stream = self.audio.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=44100,  # Changed from 16000 to 44100 for better quality
                    input=True,
                    frames_per_buffer=CHUNK_SIZE
                )
                print("Audio input started")
                threading.Thread(target=self.audio_stream_loop, daemon=True).start()
            except Exception as e:
                print(f"Audio input error: {e}")
                if hasattr(self, 'gui'):
                    messagebox.showerror("Audio Error", f"Could not start microphone: {e}")

    def audio_stream_loop(self):
        packet_count = 0
        while self.running and self.input_stream and self.input_stream.is_active():
            try:
                data = self.input_stream.read(CHUNK_SIZE, exception_on_overflow=False)
                self.send_udp(MessageType.AUDIO_STREAM, data)
                packet_count += 1
                if packet_count % 100 == 0:  # Log every 100 packets
                    print(f"Sent {packet_count} audio packets")
                # Small delay to reduce bandwidth
                time.sleep(0.002)
            except Exception as e:
                if self.running:
                    print(f"Audio capture error: {e}")
                break
        print("Audio stream stopped")

    def stop_audio_stream(self):
        if self.input_stream:
            self.input_stream.stop_stream()
            self.input_stream.close()
            self.input_stream = None

    def play_audio(self, audio_bytes):
        if self.output_stream is None:
            if self.audio is None:
                self.audio = pyaudio.PyAudio()
            try:
                # Print devices once to help pick correct output (e.g., headphones)
                if not hasattr(self, '_printed_devices'):
                    list_audio_devices(self.audio)
                    self._printed_devices = True

                device_index = get_default_output_device_index(self.audio)
                open_kwargs = dict(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=44100,  # match input
                    output=True,
                    frames_per_buffer=CHUNK_SIZE
                )
                if device_index is not None:
                    open_kwargs['output_device_index'] = device_index

                self.output_stream = self.audio.open(**open_kwargs)
                try:
                    dev_info = (self.audio.get_device_info_by_index(device_index)
                                if device_index is not None else None)
                    if dev_info:
                        print(f"Audio output initialized on: {dev_info.get('name')}")
                    else:
                        print("Audio output initialized (default device)")
                except Exception:
                    print("Audio output initialized")
            except Exception as e:
                print(f"Audio output error: {e}")
                return
        
        try:
            if self.output_stream and self.output_stream.is_active():
                self.output_stream.write(audio_bytes)
            if not hasattr(self, '_audio_packet_count'):
                self._audio_packet_count = 0
            self._audio_packet_count += 1
            if self._audio_packet_count % 100 == 0:
                print(f"Received {self._audio_packet_count} audio packets")
        except Exception as e:
            print(f"Audio play error: {e}")

    def share_screen(self):
        self.is_sharing = True
        self.send_tcp(MessageType.SCREEN_START, {"user": self.username})
        threading.Thread(target=self.screen_share_loop, daemon=True).start()

    def screen_share_loop(self):
        while self.running and self.is_sharing:
            try:
                img = ImageGrab.grab()
                # Resize for bandwidth
                img = img.resize((800, 600), Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                img.save(buffer, format='JPEG', quality=60)
                img_data = base64.b64encode(buffer.getvalue()).decode()
                self.send_tcp(MessageType.SCREEN_IMAGE, {"image": img_data, "user": self.username})
                time.sleep(0.5)  # 2 FPS for screen
            except Exception as e:
                print(f"Screen share error: {e}")
                break

    def stop_share_screen(self):
        self.is_sharing = False
        self.send_tcp(MessageType.SCREEN_STOP, {"user": self.username})

    def send_chat(self, message):
        payload = {"msg": message}
        self.send_tcp(MessageType.CHAT, payload)

    def share_file(self, filepath):
        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)
        total_chunks = max(1, math.ceil(filesize / 4096))
        
        # Notify about file
        self.send_tcp(MessageType.FILE_NOTIFY, {"filename": filename, "size": filesize})
        if hasattr(self, 'gui') and self.gui:
            self.gui.root.after(0, self.gui.begin_file_upload, filename, filesize, total_chunks)
        
        # Send chunks
        with open(filepath, 'rb') as f:
            chunk_id = 0
            while True:
                chunk = f.read(4096)
                if not chunk:
                    break
                chunk_data = base64.b64encode(chunk).decode()
                self.send_tcp(MessageType.FILE_CHUNK, {
                    "filename": filename,
                    "chunk_id": chunk_id,
                    "data": chunk_data
                })
                if hasattr(self, 'gui') and self.gui:
                    self.gui.root.after(0, self.gui.update_file_upload_progress, filename, chunk_id + 1, total_chunks)
                chunk_id += 1
                time.sleep(0.01)  # Small delay to avoid flooding
        if hasattr(self, 'gui') and self.gui:
            self.gui.root.after(0, self.gui.finish_file_upload, filename)


class GUI:
    def __init__(self, client):
        self.client = client
        self.root = tk.Tk()
        self.root.title(f"{client.username} • LAN Collaboration Suite")
        self.root.geometry("1280x760")
        self.root.minsize(1100, 650)

        self.setup_styles()

        self.permission_state = {'camera': None, 'microphone': None, 'screen': None}
        self.video_tiles = {}
        self.focus_user = None
        self.file_chunks = {}
        self.incoming_files_meta = {}
        self.file_rows = {}
        self.participants = {}
        self.participant_states = {}
        self._speaker_tokens = {}
        self.video_active = False
        self.mic_active = False
        self.sharing = False
        self.current_presenter = None
        self.presenter_photo = None
        self.meeting_start = time.time()
        self.outgoing_file = None
        self.sidebar_visible = False
        self.view_mode = "gallery"  # Start with gallery view showing videos

        self.file_send_status_var = tk.StringVar(value="Ready to collaborate")
        self.timer_value = tk.StringVar(value="00:00")
        self.speaker_var = tk.StringVar(value="No active speaker")
        self.mic_state_var = tk.StringVar(value="Mic: muted")
        self.cam_state_var = tk.StringVar(value="Camera: off")
        self.screen_state_var = tk.StringVar(value="Screen share: idle")
        self.participant_count_var = tk.StringVar(value="Participants: 0")
        self.focus_banner_var = tk.StringVar(value="Focus view inactive")

        self._grid_dims = (0, 0)

        self.build_layout()
        self.update_meeting_timer()

        self.add_participant(self.client.username, is_self=True)
        self.update_participant_status(self.client.username, 'mic', False)
        self.update_participant_status(self.client.username, 'video', False)
        
        print(f"🚀 GUI initialized in {self.view_mode} mode")
        
        # Ensure gallery view is visible on startup
        self.video_canvas_container.grid()
        self.speaker_frame.grid_remove()
        self.screen_share_frame.grid_remove()
        
        # Force gallery view to be visible at start
        def check_visibility():
            print(f"🔍 Checking visibility after 100ms:")
            print(f"   video_container visible: {self.video_container.winfo_ismapped()}")
            print(f"   video_canvas_container visible: {self.video_canvas_container.winfo_ismapped()}")
            print(f"   video_canvas visible: {self.video_canvas.winfo_ismapped()}")
            print(f"   video_grid_frame visible: {self.video_grid_frame.winfo_ismapped()}")
            print(f"   speaker_frame visible: {self.speaker_frame.winfo_ismapped()}")
            print(f"   Current view_mode: {self.view_mode}")
            self.reflow_video_grid()
        
        self.root.after(100, check_visibility)

    def build_layout(self):
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        # Top bar (Zoom-like minimal header)
        self.header = ttk.Frame(self.root, style='ZoomHeader.TFrame', padding=(16, 8))
        self.header.grid(row=0, column=0, sticky='ew')
        self.header.columnconfigure(1, weight=1)

        # Left: Meeting info
        ttk.Label(self.header, textvariable=self.timer_value, style='ZoomTimer.TLabel').grid(row=0, column=0, padx=(0, 16))
        
        # Center: Room name
        ttk.Label(self.header, text=f"{self.client.username}'s Meeting", style='ZoomTitle.TLabel').grid(row=0, column=1)
        
        # Right: View controls
        controls_frame = ttk.Frame(self.header, style='ZoomHeader.TFrame')
        controls_frame.grid(row=0, column=2, padx=(16, 0))
        ttk.Button(controls_frame, text="🎭 Speaker View" if self.view_mode == "gallery" else "🎬 Gallery View", 
                   style='ZoomHeaderBtn.TButton', command=self.toggle_view_mode, width=16).pack(side='left', padx=4)
        ttk.Button(controls_frame, text="💬 Chat", style='ZoomHeaderBtn.TButton', 
                   command=self.toggle_sidebar, width=10).pack(side='left', padx=4)
        ttk.Button(controls_frame, text="👥 Participants", style='ZoomHeaderBtn.TButton',
                   command=lambda: self.toggle_sidebar("participants"), width=14).pack(side='left', padx=4)

        # Main content area
        self.content_frame = ttk.Frame(self.root, style='ZoomBody.TFrame')
        self.content_frame.grid(row=1, column=0, sticky='nsew')
        self.content_frame.grid_rowconfigure(0, weight=1)
        self.content_frame.grid_columnconfigure(0, weight=1)

        # Video area (main focus - like Zoom)
        self.video_container = ttk.Frame(self.content_frame, style='ZoomBody.TFrame')
        self.video_container.grid(row=0, column=0, sticky='nsew', padx=8, pady=8)
        self.video_container.grid_rowconfigure(0, weight=1)
        self.video_container.grid_columnconfigure(0, weight=1)

        # Speaker view (single large video)
        self.speaker_frame = ttk.Frame(self.video_container, style='ZoomSpeaker.TFrame')
        self.speaker_frame.grid(row=0, column=0, sticky='nsew')
        self.speaker_frame.grid_rowconfigure(0, weight=1)
        self.speaker_frame.grid_columnconfigure(0, weight=1)
        self.speaker_label = tk.Label(self.speaker_frame, text="No active speaker", 
                                      bg='#1a1a1a', fg='#999', font=('Segoe UI', 16))
        self.speaker_label.grid(row=0, column=0, sticky='nsew', padx=20, pady=20)
        self.speaker_name = ttk.Label(self.speaker_frame, text="", style='ZoomSpeakerName.TLabel')
        self.speaker_name.grid(row=1, column=0, sticky='ew', padx=20, pady=(0, 20))
        self.speaker_frame.grid_remove()  # Hidden by default

        # Screen sharing view (uses speaker frame space when someone shares)
        self.screen_share_frame = ttk.Frame(self.video_container, style='ZoomSpeaker.TFrame')
        self.screen_share_frame.grid(row=0, column=0, sticky='nsew')
        self.screen_share_frame.grid_rowconfigure(1, weight=1)
        self.screen_share_frame.grid_columnconfigure(0, weight=1)
        self.presenter_title = ttk.Label(self.screen_share_frame, text="No one is sharing", 
                                        style='ZoomPresenterTitle.TLabel')
        self.presenter_title.grid(row=0, column=0, sticky='ew', padx=20, pady=(20, 10))
        self.screen_display = tk.Label(self.screen_share_frame, text="Screen share will appear here", 
                                       bg='#000000', fg='#666', font=('Segoe UI', 14))
        self.screen_display.grid(row=1, column=0, sticky='nsew', padx=20, pady=(0, 20))
        self.screen_share_frame.grid_remove()  # Hidden by default

        # Gallery view (grid of videos)
        self.video_canvas_container = ttk.Frame(self.video_container, style='ZoomBody.TFrame')
        self.video_canvas_container.grid(row=0, column=0, sticky='nsew')
        self.video_canvas_container.grid_rowconfigure(0, weight=1)
        self.video_canvas_container.grid_columnconfigure(0, weight=1)

        self.video_canvas = tk.Canvas(self.video_canvas_container, bg='#1a1a1a', highlightthickness=0)
        self.video_canvas.grid(row=0, column=0, sticky='nsew')
        self.video_scroll = ttk.Scrollbar(self.video_canvas_container, orient='vertical', command=self.video_canvas.yview)
        self.video_scroll.grid(row=0, column=1, sticky='ns')
        self.video_canvas.configure(yscrollcommand=self.video_scroll.set)
        self.video_grid_frame = ttk.Frame(self.video_canvas, style='ZoomGallery.TFrame')
        self.video_canvas.create_window((0, 0), window=self.video_grid_frame, anchor='nw')
        self.video_grid_frame.bind('<Configure>', lambda e: self.video_canvas.configure(scrollregion=self.video_canvas.bbox('all')))

        # Sidebar (collapsible - like Zoom's chat/participants panel)
        self.sidebar_container = ttk.Frame(self.content_frame, style='ZoomSidebar.TFrame')
        self.sidebar_container.grid(row=0, column=1, sticky='nsew', padx=(0, 8), pady=8)
        self.sidebar_container.grid_rowconfigure(0, weight=1)
        self.sidebar_container.grid_columnconfigure(0, weight=1)
        self.sidebar_container.grid_remove()  # Hidden by default

        self.sidebar = ttk.Notebook(self.sidebar_container, style='ZoomTabs.TNotebook')
        self.sidebar.grid(row=0, column=0, sticky='nsew')
        
        # Chat tab
        chat_tab = ttk.Frame(self.sidebar, style='ZoomTab.TFrame')
        self.sidebar.add(chat_tab, text='💬 Chat')
        chat_tab.grid_rowconfigure(0, weight=1)
        chat_tab.grid_columnconfigure(0, weight=1)
        
        self.chat_text = tk.Text(chat_tab, wrap='word', state='disabled', bg='#262626', fg='#e0e0e0', 
                                insertbackground='#0b5cff', relief='flat', bd=0, font=('Segoe UI', 10))
        self.chat_text.grid(row=0, column=0, sticky='nsew', padx=12, pady=(12, 8))
        self.chat_text.tag_config('system', foreground='#0b5cff', font=('Segoe UI', 9, 'italic'))
        self.chat_text.tag_config('username', foreground='#0b5cff', font=('Segoe UI', 10, 'bold'))
        
        chat_entry_frame = ttk.Frame(chat_tab, style='ZoomTab.TFrame')
        chat_entry_frame.grid(row=1, column=0, sticky='ew', padx=12, pady=(0, 12))
        chat_entry_frame.grid_columnconfigure(0, weight=1)
        
        self.chat_entry = tk.Entry(chat_entry_frame, relief='solid', bd=1, bg='#333', fg='#fff', 
                                   insertbackground='#0b5cff', font=('Segoe UI', 10))
        self.chat_entry.grid(row=0, column=0, sticky='ew', ipady=6)
        self.chat_entry.bind('<Return>', self.send_chat_cb)
        ttk.Button(chat_entry_frame, text="Send", style='ZoomAccent.TButton', 
                  command=lambda: self.send_chat_cb(None)).grid(row=0, column=1, padx=(8, 0))

        # Participants tab
        participants_tab = ttk.Frame(self.sidebar, style='ZoomTab.TFrame')
        self.sidebar.add(participants_tab, text='👥 Participants')
        participants_tab.grid_rowconfigure(1, weight=1)
        participants_tab.grid_columnconfigure(0, weight=1)
        
        part_header = ttk.Frame(participants_tab, style='ZoomTab.TFrame')
        part_header.grid(row=0, column=0, sticky='ew', padx=12, pady=(12, 8))
        ttk.Label(part_header, textvariable=self.participant_count_var, style='ZoomParticipantCount.TLabel').pack(anchor='w')
        
        self.participants_tree = ttk.Treeview(participants_tab, columns=('status',), show='tree', height=15, selectmode='none')
        self.participants_tree.heading('#0', text='Name')
        self.participants_tree.column('#0', width=150)
        self.participants_tree.column('status', width=150, anchor='w')
        self.participants_tree.grid(row=1, column=0, sticky='nsew', padx=12, pady=(0, 12))

        # Files tab  
        files_tab = ttk.Frame(self.sidebar, style='ZoomTab.TFrame')
        self.sidebar.add(files_tab, text='📁 Files')
        files_tab.grid_rowconfigure(1, weight=1)
        files_tab.grid_columnconfigure(0, weight=1)
        
        ttk.Button(files_tab, text="📤 Share File", style='ZoomAccent.TButton', 
                  command=self.share_file_cb).grid(row=0, column=0, sticky='ew', padx=12, pady=12)
        
        files_scroll_container = ttk.Frame(files_tab, style='ZoomTab.TFrame')
        files_scroll_container.grid(row=1, column=0, sticky='nsew', padx=12, pady=(0, 12))
        files_scroll_container.grid_rowconfigure(0, weight=1)
        files_scroll_container.grid_columnconfigure(0, weight=1)
        
        self.files_canvas = tk.Canvas(files_scroll_container, bg='#262626', highlightthickness=0)
        self.files_canvas.grid(row=0, column=0, sticky='nsew')
        self.files_scroll = ttk.Scrollbar(files_scroll_container, orient='vertical', command=self.files_canvas.yview)
        self.files_scroll.grid(row=0, column=1, sticky='ns')
        self.files_canvas.configure(yscrollcommand=self.files_scroll.set)
        self.files_frame = ttk.Frame(self.files_canvas, style='ZoomTab.TFrame')
        self.files_canvas.create_window((0, 0), window=self.files_frame, anchor='nw')
        self.files_frame.bind('<Configure>', lambda e: self.files_canvas.configure(scrollregion=self.files_canvas.bbox('all')))

        # Bottom control bar (Zoom-style centered buttons)
        self.control_bar = ttk.Frame(self.root, style='ZoomControls.TFrame', padding=(20, 12))
        self.control_bar.grid(row=2, column=0, sticky='ew')
        self.control_bar.grid_columnconfigure(0, weight=1)
        self.control_bar.grid_columnconfigure(1, weight=0)
        self.control_bar.grid_columnconfigure(2, weight=1)

        # Center buttons
        buttons_frame = ttk.Frame(self.control_bar, style='ZoomControls.TFrame')
        buttons_frame.grid(row=0, column=1)

        self.mic_btn = ttk.Button(buttons_frame, text="🎤\nUnmute", style='ZoomControl.TButton', command=self.toggle_mic, width=10)
        self.mic_btn.grid(row=0, column=0, padx=4)
        
        self.video_btn = ttk.Button(buttons_frame, text="📹\nStart Video", style='ZoomControl.TButton', command=self.toggle_video, width=10)
        self.video_btn.grid(row=0, column=1, padx=4)
        
        self.screen_btn = ttk.Button(buttons_frame, text="🖥️\nShare Screen", style='ZoomControl.TButton', command=self.toggle_share, width=12)
        self.screen_btn.grid(row=0, column=2, padx=4)
        
        # Camera selector (left side)
        cam_frame = ttk.Frame(self.control_bar, style='ZoomControls.TFrame')
        cam_frame.grid(row=0, column=0, sticky='w')
        self.camera_indices, self.camera_names = self.detect_cameras()
        ttk.Label(cam_frame, text="📷", style='ZoomControlLabel.TLabel').pack(side='left', padx=(0, 4))
        self.camera_select = ttk.Combobox(cam_frame, values=self.camera_names or ["No camera"], 
                                         state='readonly', width=18, style='ZoomCombo.TCombobox')
        self.camera_select.pack(side='left')
        if self.camera_names:
            self.camera_select.current(0)
        else:
            self.camera_select.set("No camera")

        # Leave button (right side)
        self.leave_btn = ttk.Button(self.control_bar, text="🚪 Leave Meeting", style='ZoomLeave.TButton', 
                                    command=self.handle_leave_meeting, width=16)
        self.leave_btn.grid(row=0, column=2, sticky='e')

    def update_meeting_timer(self):
        elapsed = max(0, int(time.time() - self.meeting_start))
        minutes, seconds = divmod(elapsed, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            self.timer_value.set(f"{hours:02}:{minutes:02}:{seconds:02}")
        else:
            self.timer_value.set(f"{minutes:02}:{seconds:02}")
        self.root.after(1000, self.update_meeting_timer)

    def toggle_sidebar(self, tab=None):
        """Toggle sidebar visibility (Zoom-like)"""
        if self.sidebar_visible:
            self.sidebar_container.grid_remove()
            self.sidebar_visible = False
        else:
            self.sidebar_container.grid()
            self.sidebar_visible = True
            if tab == "participants":
                self.sidebar.select(1)
            elif tab == "files":
                self.sidebar.select(2)
            else:
                self.sidebar.select(0)

    def toggle_view_mode(self):
        """Toggle between gallery and speaker view (Zoom-like)"""
        if self.view_mode == "gallery":
            self.view_mode = "speaker"
            self.speaker_frame.grid()
            self.video_canvas_container.grid_remove()
            self.screen_share_frame.grid_remove()
            self.update_speaker_view()
        else:
            self.view_mode = "gallery"
            self.speaker_frame.grid_remove()
            self.screen_share_frame.grid_remove()
            self.video_canvas_container.grid()
        self.reflow_video_grid()

    def ensure_permission(self, key, friendly_name):
        state = self.permission_state.get(key)
        if state is None:
            allow = messagebox.askyesno("Permission request", f"This action requires access to your {friendly_name}. Allow?")
            self.permission_state[key] = allow
            if not allow:
                self.file_send_status_var.set(f"{friendly_name.capitalize()} permission denied")
                self.root.after(4000, lambda: self.file_send_status_var.set("Ready to collaborate"))
            return allow
        return state

    def get_selected_camera_index(self):
        if not self.camera_indices:
            return None
        idx = self.camera_select.current()
        if idx is None or idx < 0:
            return None
        return self.camera_indices[idx]

    def toggle_video(self):
        if not self.video_active:
            if not self.ensure_permission('camera', 'camera'):
                return
            device_index = self.get_selected_camera_index()
            self.client.start_video_stream(device_index=device_index)
            if self.client.cap:
                self.video_active = True
                self.video_btn.config(text="📹\nStop Video")
                self.cam_state_var.set("Camera: on")
                self.update_participant_status(self.client.username, 'video', True)
            else:
                self.cam_state_var.set("Camera: unavailable")
        else:
            self.client.stop_video_stream()
            self.video_active = False
            self.video_btn.config(text="📹\nStart Video")
            self.cam_state_var.set("Camera: off")
            self.update_participant_status(self.client.username, 'video', False)

    def toggle_mic(self):
        if not self.mic_active:
            if not self.ensure_permission('microphone', 'microphone'):
                return
            self.client.start_audio_stream()
            self.mic_active = True
            self.mic_btn.config(text="🎤\nMute")
            self.mic_state_var.set("Mic: live")
            self.update_participant_status(self.client.username, 'mic', True)
        else:
            self.client.stop_audio_stream()
            self.mic_active = False
            self.mic_btn.config(text="🎤\nUnmute")
            self.mic_state_var.set("Mic: muted")
            self.update_participant_status(self.client.username, 'mic', False)

    def toggle_share(self):
        if not self.sharing:
            if not self.ensure_permission('screen', 'screen'):
                return
            self.client.share_screen()
            self.sharing = True
            self.screen_btn.config(text="🖥️\nStop Sharing")
            self.screen_state_var.set("Screen share: live")
            self.set_presenter(self.client.username)
        else:
            self.client.stop_share_screen()
            self.sharing = False
            self.screen_btn.config(text="🖥️\nShare Screen")
            self.screen_state_var.set("Screen share: idle")
            if self.current_presenter == self.client.username:
                self.clear_presenter(self.client.username)

    def send_chat_cb(self, event):
        message = self.chat_entry.get().strip()
        if message:
            self.client.send_chat(message)
            # Don't add message locally - server will broadcast it back to us
            self.chat_entry.delete(0, tk.END)

    def share_file_cb(self):
        filepath = filedialog.askopenfilename()
        if filepath:
            self.file_send_status_var.set(f"Preparing {os.path.basename(filepath)}...")
            threading.Thread(target=self.client.share_file, args=(filepath,), daemon=True).start()

    def begin_file_upload(self, filename, size, total_chunks):
        self.outgoing_file = {'name': filename, 'size': size, 'total_chunks': total_chunks}
        self.file_send_status_var.set(f"Sending {filename} (0%)")

    def update_file_upload_progress(self, filename, sent_chunks, total_chunks):
        if not self.outgoing_file or self.outgoing_file['name'] != filename:
            return
        percent = int((sent_chunks / max(1, total_chunks)) * 100)
        self.file_send_status_var.set(f"Sending {filename} ({percent}%)")
        if percent >= 100:
            self.file_send_status_var.set(f"Sent {filename}")

    def finish_file_upload(self, filename):
        if self.outgoing_file and self.outgoing_file['name'] == filename:
            self.file_send_status_var.set(f"Sent {filename}")
            self.outgoing_file = None
            self.root.after(4000, lambda: self.file_send_status_var.set("Ready to collaborate"))

    def handle_message(self, msg_type, payload):
        if msg_type == MessageType.CHAT:
            self.add_chat_message(payload['user'], payload['msg'])
        elif msg_type == MessageType.SCREEN_START:
            presenter = payload['user']
            self.set_presenter(presenter)
            self.add_chat_message("System", f"{presenter} started sharing", system=True)
        elif msg_type == MessageType.SCREEN_IMAGE:
            self.update_presenter_image(payload.get('image'), payload.get('user'))
        elif msg_type == MessageType.SCREEN_STOP:
            presenter = payload['user']
            self.clear_presenter(presenter)
            self.add_chat_message("System", f"{presenter} stopped sharing", system=True)
        elif msg_type == MessageType.FILE_NOTIFY:
            filename = payload['filename']
            sender = payload.get('user', 'Someone')
            size = payload.get('size', 0)
            self.incoming_files_meta[filename] = {'sender': sender, 'size': size, 'received': 0}
            self.file_chunks[filename] = []
            self.create_file_row(filename, sender, size)
        elif msg_type == MessageType.FILE_CHUNK:
            filename = payload['filename']
            chunk_id = payload['chunk_id']
            data = base64.b64decode(payload['data'])
            info = self.incoming_files_meta.setdefault(filename, {'sender': 'Unknown', 'size': 0, 'received': 0})
            self.file_chunks.setdefault(filename, []).append((chunk_id, data))
            info['received'] += len(data)
            self.update_file_row_progress(filename, info['received'], info.get('size', 0))
        elif msg_type == MessageType.USER_JOIN:
            username = payload['user']
            self.add_participant(username)
            self.add_chat_message("System", f"{username} joined", system=True)
        elif msg_type == MessageType.USER_LEAVE:
            username = payload['user']
            self.remove_participant(username)
            self.add_chat_message("System", f"{username} left", system=True)

    def add_chat_message(self, user, msg, system=False):
        self.chat_text.config(state='normal')
        if system:
            self.chat_text.insert(tk.END, f"*** {msg} ***\n", 'system')
        else:
            self.chat_text.insert(tk.END, f"{user}: ", 'username')
            self.chat_text.insert(tk.END, f"{msg}\n")
        self.chat_text.see(tk.END)
        self.chat_text.config(state='disabled')

    def update_speaker_view(self):
        """Update the speaker view with the most recent speaker"""
        if not self.video_tiles:
            self.speaker_label.config(text="No participants yet")
            self.speaker_name.config(text="")
            return
        
        # Find most recent speaker or just show first participant
        speaker_username = list(self.video_tiles.keys())[0]
        if speaker_username in self.video_tiles:
            tile = self.video_tiles[speaker_username]
            image = tile.get('image')
            if image:
                self.speaker_label.config(image=image, text='')
                self.speaker_label.image = image
            else:
                self.speaker_label.config(image='', text=f"Waiting for video from {speaker_username}...")
                self.speaker_label.image = None
            self.speaker_name.config(text=speaker_username)

    def _create_video_tile(self, username):
        tile_frame = ttk.Frame(self.video_grid_frame, style='ZoomVideoTile.TFrame', padding=4)
        tile_frame.configure(cursor='hand2')
        
        # Video display label - no fixed size, will adapt to image
        label = tk.Label(tile_frame, text="Waiting for video...", bg='#1a1a1a', fg='#666', 
                        font=('Segoe UI', 10), width=40, height=20)
        label.pack(fill='both', expand=True)
        
        # Name label overlay (bottom-left like Zoom) - using semi-transparent effect
        name_overlay = tk.Frame(label, bg='#000000')
        name_overlay.place(relx=0, rely=1, anchor='sw', x=8, y=-8)
        name_label = tk.Label(name_overlay, text=username, bg='#000000', fg='white',
                             font=('Segoe UI', 9, 'bold'), padx=8, pady=4)
        name_label.pack()
        
        tile = {'frame': tile_frame, 'label': label, 'name': name_label, 'image': None}
        for widget in (tile_frame, label):
            widget.bind('<Button-1>', lambda e, u=username: self.toggle_focus_view(u))
            widget.bind('<Double-Button-1>', lambda e, u=username: self.toggle_view_mode())
        self.video_tiles[username] = tile
        return tile

    def toggle_focus_view(self, username):
        """Single click on video - deprecated in Zoom-like design, keeping for compatibility"""
        pass  # In real Zoom, single click doesn't do much

    def reflow_video_grid(self):
        """Reflow video tiles in Zoom-like gallery grid"""
        tiles = list(self.video_tiles.items())
        prev_rows, prev_cols = self._grid_dims
        
        print(f"🔄 Reflowing video grid with {len(tiles)} tiles, view_mode={self.view_mode}")
        
        # Clear old grid
        for r in range(prev_rows):
            self.video_grid_frame.grid_rowconfigure(r, weight=0)
        for c in range(prev_cols):
            self.video_grid_frame.grid_columnconfigure(c, weight=0)
        for child in self.video_grid_frame.winfo_children():
            child.grid_forget()
        
        count = len(tiles)
        if not count:
            self._grid_dims = (0, 0)
            print("⚠️ No tiles to display")
            return
        
        # Calculate optimal grid layout (Zoom uses up to 5 columns in gallery)
        cols = min(5, max(1, int(math.ceil(math.sqrt(count)))))
        for idx, (username, tile) in enumerate(tiles):
            r = idx // cols
            c = idx % cols
            tile['frame'].grid(row=r, column=c, padx=4, pady=4, sticky='nsew')
            print(f"  📍 Placed {username} at grid ({r}, {c})")
        
        row_count = int(math.ceil(count / cols))
        for r in range(row_count):
            self.video_grid_frame.rowconfigure(r, weight=1)
        for c in range(cols):
            self.video_grid_frame.columnconfigure(c, weight=1)
        
        self._grid_dims = (row_count, cols)
        print(f"✅ Grid configured: {row_count} rows x {cols} cols")

    def refresh_tile_styles(self):
        """No longer needed - Zoom doesn't highlight focused tiles"""
        pass

    def update_focus_display(self):
        """No longer needed - using speaker/gallery views instead"""
        pass

    def update_video(self, frame_bytes, sender_username):
        try:
            print(f"📹 Received video from {sender_username}, {len(frame_bytes)} bytes")
            nparr = np.frombuffer(frame_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is None:
                print(f"❌ Failed to decode frame from {sender_username}")
                return
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame)
            
            # Resize to reasonable size for display
            img = img.resize((320, 240), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image=img)
            
            # Create tile if it doesn't exist
            if sender_username not in self.video_tiles:
                print(f"✨ Creating new video tile for {sender_username}")
                self.add_participant(sender_username)
                tile = self._create_video_tile(sender_username)
                self.reflow_video_grid()
                print(f"✅ Video tile created and grid reflowed. Total tiles: {len(self.video_tiles)}")
            else:
                tile = self.video_tiles[sender_username]
            
            # Update the label with the video frame
            tile['label'].config(image=photo, text='', bg='#000000')
            tile['label'].image = photo  # Keep a reference to prevent garbage collection
            tile['image'] = photo
            print(f"🖼️ Updated video tile for {sender_username}")
            
            # Update participant status
            self.update_participant_status(sender_username, 'video', True)
            
            # Update speaker view if in speaker mode
            if self.view_mode == "speaker":
                self.update_speaker_view()
        except Exception as exc:
            print(f"❌ Video update error: {exc}")
            import traceback
            traceback.print_exc()

    def update_speaker(self, username):
        token = object()
        self._speaker_tokens[username] = token
        self.speaker_var.set(f"{username} is speaking")
        if username != self.client.username:
            self.update_participant_status(username, 'mic', True)

        def clear():
            if self._speaker_tokens.get(username) is token:
                self._speaker_tokens.pop(username, None)
                if username != self.client.username:
                    self.update_participant_status(username, 'mic', False)
                if not self._speaker_tokens:
                    self.speaker_var.set("No active speaker")

        self.root.after(2000, clear)

    def update_participant_count(self):
        total = len(self.participants)
        self.participant_count_var.set(f"Participants: {total}")

    def add_participant(self, username, is_self=False):
        if username in self.participants:
            return
        icon = '👤 (You)' if is_self else '👤'
        item_id = self.participants_tree.insert('', 'end', text=f"{icon} {username}", values=('Connecting...',))
        if is_self:
            self.participants_tree.item(item_id, tags=('self',))
            self.participants_tree.tag_configure('self', foreground='#0b5cff')
        self.participants[username] = item_id
        self.participant_states[username] = {'mic': False, 'video': False}
        self.update_participant_count()

    def remove_participant(self, username):
        item_id = self.participants.pop(username, None)
        if item_id:
            self.participants_tree.delete(item_id)
        self.participant_states.pop(username, None)
        tile = self.video_tiles.pop(username, None)
        if tile:
            tile['frame'].destroy()
            self.reflow_video_grid()
        if self.current_presenter == username:
            self.clear_presenter(username)
        self.update_participant_count()

    def update_participant_status(self, username, field, value):
        state = self.participant_states.setdefault(username, {'mic': False, 'video': False})
        state[field] = value
        item_id = self.participants.get(username)
        if not item_id:
            return
        status_parts = []
        if state['video']:
            status_parts.append('📹')
        if state['mic']:
            status_parts.append('🎤')
        else:
            status_parts.append('🔇')
        status_text = ' '.join(status_parts) if status_parts else 'Offline'
        self.participants_tree.set(item_id, 'status', status_text)

    def create_file_row(self, filename, sender, size):
        row_frame = ttk.Frame(self.files_frame, style='ZoomFileRow.TFrame', padding=(10, 10))
        row_frame.pack(fill='x', pady=6)
        
        header = ttk.Frame(row_frame, style='ZoomFileRow.TFrame')
        header.pack(fill='x')
        ttk.Label(header, text=f"📄 {filename}", style='ZoomFileLabel.TLabel').pack(side='left')
        ttk.Label(header, text=f"from {sender}", style='ZoomFileCaption.TLabel').pack(side='right')
        
        size_text = f"{size/1024:.1f} KB" if size else "Unknown size"
        ttk.Label(row_frame, text=size_text, style='ZoomFileCaption.TLabel').pack(anchor='w', pady=(4, 0))
        
        progress_var = tk.DoubleVar(value=0)
        progress = ttk.Progressbar(row_frame, variable=progress_var, maximum=100, style='ZoomProgress.Horizontal.TProgressbar')
        progress.pack(fill='x', pady=(8, 6))
        
        progress_label = ttk.Label(row_frame, text="Waiting...", style='ZoomFileCaption.TLabel')
        progress_label.pack(anchor='w')
        
        action_btn = ttk.Button(row_frame, text="💾 Download", style='ZoomAccent.TButton', 
                               command=lambda: self.download_file(filename), state='disabled')
        action_btn.pack(anchor='e', pady=(8, 0))
        
        self.file_rows[filename] = {
            'frame': row_frame,
            'progress_var': progress_var,
            'progress_label': progress_label,
            'button': action_btn,
            'sender': sender,
            'size': size
        }

    def update_file_row_progress(self, filename, received_bytes, total_bytes):
        row = self.file_rows.get(filename)
        info = self.incoming_files_meta.get(filename)
        if not row:
            return
        percent = min(100, (received_bytes / total_bytes) * 100) if total_bytes else 0
        row['progress_var'].set(percent)
        if total_bytes:
            row['progress_label'].config(text=f"{percent:.0f}% ({received_bytes/1024:.1f} KB / {total_bytes/1024:.1f} KB)")
        else:
            row['progress_label'].config(text=f"{received_bytes/1024:.1f} KB received")
        if total_bytes and received_bytes >= total_bytes:
            row['button'].configure(state='normal')
            row['progress_label'].config(text="Ready to download")
            sender = info.get('sender', 'a teammate') if info else 'a teammate'
            self.file_send_status_var.set(f"{filename} ready from {sender}")
            self.root.after(5000, lambda: self.file_send_status_var.set("Ready to collaborate"))

    def set_presenter(self, username):
        self.current_presenter = username
        self.presenter_title.config(text=f"{username} is sharing")
        self.screen_display.config(text="Waiting for screen frames...", image='', bg='#000000')
        self.screen_display.image = None
        
        # Show screen share frame, hide others
        self.screen_share_frame.grid()
        self.speaker_frame.grid_remove()
        self.video_canvas_container.grid_remove()

    def update_presenter_image(self, image_b64, presenter):
        if not image_b64:
            return
        try:
            img_data = base64.b64decode(image_b64)
            img = Image.open(io.BytesIO(img_data))
            img.thumbnail((1200, 700), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self.screen_display.config(image=photo, text='', bg='#000000')
            self.screen_display.image = photo
            self.presenter_photo = photo
            if presenter:
                self.presenter_title.config(text=f"{presenter} is sharing")
                self.current_presenter = presenter
        except Exception as exc:
            print(f"Screen image error: {exc}")

    def clear_presenter(self, username):
        if self.current_presenter and self.current_presenter != username:
            return
        self.current_presenter = None
        self.presenter_title.config(text="No one is sharing")
        self.screen_display.config(text="Screen share will appear here", image='', bg='#000000')
        self.screen_display.image = None
        self.presenter_photo = None
        
        # Hide screen share frame, show appropriate view
        self.screen_share_frame.grid_remove()
        if self.view_mode == "speaker":
            self.speaker_frame.grid()
            self.video_canvas_container.grid_remove()
            self.update_speaker_view()
        else:
            # Gallery view
            self.video_canvas_container.grid()
            self.speaker_frame.grid_remove()

    def handle_leave_meeting(self):
        if messagebox.askyesno("Leave meeting", "Are you sure you want to leave this session?"):
            self.client.on_closing()

    def download_file(self, filename):
        chunks = self.file_chunks.get(filename)
        info = self.incoming_files_meta.get(filename)
        if not chunks or not info or info.get('received', 0) < info.get('size', 0):
            messagebox.showwarning("File transfer", "File is still downloading. Please wait a moment.")
            return
        chunks = sorted(chunks, key=lambda x: x[0])
        data = b''.join(chunk for _, chunk in chunks)
        save_path = filedialog.asksaveasfilename(defaultextension=os.path.splitext(filename)[1], initialfile=filename)
        if save_path:
            try:
                with open(save_path, 'wb') as file_obj:
                    file_obj.write(data)
                messagebox.showinfo("File saved", f"Saved to {save_path}")
            except Exception as exc:
                messagebox.showerror("File error", f"Could not save file: {exc}")

    def detect_cameras(self, max_index=2):  # Reduced from 5 to 2 for faster detection
        indices = []
        names = []
        
        # Try only first few indices to avoid excessive warnings
        for i in range(max_index + 1):
            try:
                # Use appropriate backend for the OS
                if IS_WINDOWS:
                    cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                elif IS_LINUX:
                    cap = cv2.VideoCapture(i, cv2.CAP_V4L2)
                else:
                    cap = cv2.VideoCapture(i)
                
                if cap.isOpened():
                    # Verify we can actually read from it
                    ret, _ = cap.read()
                    if ret:
                        indices.append(i)
                        names.append(f"Camera {i}")
                cap.release()
            except Exception as e:
                print(f"Camera {i} detection error: {e}")
                continue
        
        # If no cameras found, add virtual camera option
        if not indices and HAS_VIRTUAL_CAMERA:
            indices.append(0)
            names.append("Virtual Camera (Test Pattern)")
        
        return indices, names

    def setup_styles(self):
        # Zoom-like color scheme
        zoom_bg = '#1a1a1a'          # Main background (dark gray)
        zoom_surface = '#262626'      # Elevated surfaces
        zoom_header = '#1f1f1f'       # Header bar
        zoom_blue = '#0b5cff'         # Zoom blue accent
        zoom_blue_hover = '#0952cc'   # Darker blue
        zoom_red = '#f55142'          # Leave button red
        zoom_red_hover = '#cc4336'    # Darker red
        zoom_text = '#e0e0e0'         # Primary text (light gray)
        zoom_text_dim = '#999'        # Secondary text
        zoom_border = '#333'          # Borders

        self.root.configure(bg=zoom_bg)
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except Exception:
            pass

        # Header styles
        style.configure('ZoomHeader.TFrame', background=zoom_header)
        style.configure('ZoomTimer.TLabel', background=zoom_header, foreground=zoom_text, 
                       font=('Segoe UI', 11, 'bold'), padding=(8, 4))
        style.configure('ZoomTitle.TLabel', background=zoom_header, foreground=zoom_text,
                       font=('Segoe UI', 12, 'bold'))
        style.configure('ZoomHeaderBtn.TButton', background=zoom_surface, foreground=zoom_text,
                       padding=(8, 6), font=('Segoe UI', 9))
        style.map('ZoomHeaderBtn.TButton', background=[('active', zoom_border)])

        # Body/Content
        style.configure('ZoomBody.TFrame', background=zoom_bg)
        style.configure('ZoomGallery.TFrame', background=zoom_bg)
        
        # Speaker view
        style.configure('ZoomSpeaker.TFrame', background=zoom_bg)
        style.configure('ZoomSpeakerName.TLabel', background=zoom_bg, foreground=zoom_text,
                       font=('Segoe UI', 14, 'bold'))
        
        # Screen share presenter
        style.configure('ZoomPresenterTitle.TLabel', background=zoom_bg, foreground=zoom_blue,
                       font=('Segoe UI', 12, 'bold'))

        # Video tiles
        style.configure('ZoomVideoTile.TFrame', background='#2d2d2d', relief='flat', borderwidth=2)
        style.configure('ZoomVideoName.TLabel', background='#2d2d2d', foreground=zoom_text,
                       font=('Segoe UI', 10))
        style.configure('ZoomVideoNameActive.TLabel', background='#2d2d2d', foreground=zoom_blue,
                       font=('Segoe UI', 10, 'bold'))

        # Sidebar
        style.configure('ZoomSidebar.TFrame', background=zoom_surface)
        style.configure('ZoomTabs.TNotebook', background=zoom_surface, borderwidth=0)
        style.configure('ZoomTab.TFrame', background=zoom_surface)
        style.configure('TNotebook.Tab', background=zoom_header, foreground=zoom_text,
                       padding=(16, 10), borderwidth=0)
        style.map('TNotebook.Tab', 
                 background=[('selected', zoom_surface), ('active', zoom_border)],
                 foreground=[('selected', zoom_blue)])
        
        style.configure('ZoomParticipantCount.TLabel', background=zoom_surface, foreground=zoom_text,
                       font=('Segoe UI Semibold', 11))
        style.configure('ZoomAccent.TButton', background=zoom_blue, foreground='white',
                       padding=(12, 8), font=('Segoe UI Semibold', 10))
        style.map('ZoomAccent.TButton', background=[('active', zoom_blue_hover)])

        # Control bar (bottom)
        style.configure('ZoomControls.TFrame', background=zoom_header)
        style.configure('ZoomControl.TButton', background=zoom_surface, foreground=zoom_text,
                       padding=(16, 12), font=('Segoe UI', 9), borderwidth=0, relief='flat')
        style.map('ZoomControl.TButton',
                 background=[('active', zoom_border), ('pressed', zoom_blue)],
                 foreground=[('active', 'white'), ('pressed', 'white')])
        
        style.configure('ZoomControlLabel.TLabel', background=zoom_header, foreground=zoom_text_dim,
                       font=('Segoe UI', 10))
        
        style.configure('ZoomLeave.TButton', background=zoom_red, foreground='white',
                       padding=(14, 10), font=('Segoe UI Semibold', 10))
        style.map('ZoomLeave.TButton', background=[('active', zoom_red_hover)])

        # Combobox
        style.configure('ZoomCombo.TCombobox', fieldbackground=zoom_surface, background=zoom_header,
                       foreground=zoom_text, arrowcolor=zoom_text, borderwidth=1, relief='solid')
        style.map('ZoomCombo.TCombobox', 
                 fieldbackground=[('readonly', zoom_surface)],
                 foreground=[('readonly', zoom_text)])

        # Treeview (participants list)
        style.configure('Treeview', background=zoom_surface, foreground=zoom_text,
                       fieldbackground=zoom_surface, bordercolor=zoom_border, borderwidth=0,
                       font=('Segoe UI', 10), rowheight=32)
        style.configure('Treeview.Heading', background=zoom_header, foreground=zoom_text,
                       font=('Segoe UI Semibold', 10))
        style.map('Treeview', background=[('selected', zoom_blue)], foreground=[('selected', 'white')])

        # File row
        style.configure('ZoomFileRow.TFrame', background=zoom_surface)
        style.configure('ZoomFileLabel.TLabel', background=zoom_surface, foreground=zoom_text,
                       font=('Segoe UI', 10))
        style.configure('ZoomFileCaption.TLabel', background=zoom_surface, foreground=zoom_text_dim,
                       font=('Segoe UI', 9))

        # Progress bar
        style.configure('ZoomProgress.Horizontal.TProgressbar', troughcolor=zoom_border,
                       bordercolor=zoom_border, background=zoom_blue, lightcolor=zoom_blue,
                       darkcolor=zoom_blue)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python client.py <server_ip> <username>")
        print("Example: python client.py 192.168.1.100 Alice")
        sys.exit(1)
    
    client = Client(sys.argv[1], sys.argv[2])