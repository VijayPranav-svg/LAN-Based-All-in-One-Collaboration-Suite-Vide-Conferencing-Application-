# LAN-Based-All-in-One-Collaboration-Suite-Vide-Conferencing-Application-

# 🖥️ LAN Video Conferencing & Collaboration Suite

A **Zoom-like LAN-based video conferencing system** built with **Python**, supporting real-time **video streaming, audio communication, chat, screen sharing, and file transfer** — all optimized for local networks (LAN).

---

## 🚀 Features

### 🎥 Video & Audio Conferencing
- Real-time **video** and **audio streaming** using OpenCV and PyAudio over UDP.
- **Low-latency** streaming optimized for LAN environments.
- Automatic **camera detection** and fallback to virtual camera if no physical device is found.
- **Speaker detection** highlights active participants.

### 💬 Chat System
- Instant text chat synchronized across all connected participants.
- Displays **system messages** (join/leave, screen sharing start/stop).

### 🖥️ Screen Sharing
- Live desktop screen broadcast with adjustable FPS (2 FPS default for bandwidth optimization).
- Automatically switches to **“Presenter View”** when someone starts sharing.

### 📁 File Transfer
- Supports large file sharing in base64-encoded chunks over TCP.
- Real-time **progress tracking** for uploads/downloads.
- Files are reconstructed and can be saved locally.

### 👥 Participants Management
- Displays all connected users with live **mic/camera status indicators**.
- Handles **join/leave notifications** and participant count dynamically.

### 🧠 Smart GUI (Tkinter)
- Fully modern, **Zoom-inspired interface** with:
  - Gallery & Speaker view modes.
  - Collapsible **chat, participants, and file panels**.
  - Custom **dark theme** styled with ttk.
- Automatically adapts layout and participant video grid.

---

## 🧩 System Architecture

| Component | Protocol | Description |
|------------|-----------|-------------|
| **Server** (`server3.py`) | TCP + UDP | Manages user sessions, relays messages, broadcasts video/audio data. |
| **Client** (`clientv3.py`) | TCP + UDP | Provides GUI, captures audio/video, handles chat, screen, and files. |

### 🔗 Communication Overview

- **TCP** is used for:
  - Chat messages  
  - File notifications & file chunks  
  - Screen sharing frames (base64 JPEG)  
  - User join/leave messages  

- **UDP** is used for:
  - Real-time **video and audio streams**

---

## 🧱 Tech Stack

| Technology | Purpose |
|-------------|----------|
| **Python 3.9+** | Core programming language |
| **OpenCV (cv2)** | Video capture, compression, and decoding |
| **PyAudio** | Microphone input and speaker output |
| **Pillow (PIL)** | Image manipulation and screen capture |
| **Tkinter** | GUI framework |
| **Sockets (TCP/UDP)** | Networking and message transport |
| **Threading** | Concurrent I/O for network and GUI |
| **JSON + struct** | Data serialization and message framing |

---

## ⚙️ Installation & Setup

### 1️⃣ Install Dependencies

Run the following command to install all required packages:

```bash
pip install opencv-python pyaudio pillow numpy
⚠️ On Linux, you may need additional system dependencies:

bash
Copy code
sudo apt install portaudio19-dev python3-tk python3-pil.imagetk
2️⃣ Start the Server
bash
Copy code
python server3.py
Expected output:

pgsql
Copy code
Server listening on TCP:5000, UDP:5001
Server started. Press Ctrl+C to stop.
3️⃣ Start Clients
On each client system (in the same LAN), run:

bash
Copy code
python clientv3.py <server_ip> <username>
Example:

bash
Copy code
python clientv3.py 192.168.1.10 Alice
python clientv3.py 192.168.1.10 Bob
✅ The GUI window will open for each user.

🧭 Usage Guide
##🎤 Audio
Click 🎤 Unmute to enable microphone streaming.

Click again to mute.

##📹 Video
Select your camera (if multiple detected).

Click 📹 Start Video to start your webcam.

Click again to stop.

##🖥️ Screen Share
Click 🖥️ Share Screen to broadcast your desktop.

Other participants will see your live screen.

Click again to stop sharing.

##💬 Chat
Type messages in the Chat panel and hit Enter.

System events (join/leave) appear automatically.

##📁 File Sharing
Go to Files tab → 📤 Share File.

Choose any local file to send to all participants.

Others can download it after full transfer.

##🧠 Internal Design Highlights
Server (server3.py)
Handles multiple clients concurrently using threading.

Uses a custom binary message format:

css
Copy code
[1 byte message_type][4 bytes payload_length][payload_bytes]
Maintains a dictionary of active clients with both TCP and UDP details.

Broadcasts TCP messages and relays UDP packets (video/audio).

Client (clientv3.py)
Runs multiple threads:

TCP listener for chat/files/screen data.

UDP listener for real-time streams.

GUI mainloop for rendering and user input.

Video/audio captured in real-time and compressed before sending.

GUI uses Tkinter’s after() to update UI safely from background threads.

##📡 Message Types (Enum)
Type	Description
CHAT	Text chat message
FILE_NOTIFY	New file transfer started
FILE_CHUNK	File data chunk
SCREEN_START / SCREEN_STOP	Screen sharing control
SCREEN_IMAGE	Screen frame data
VIDEO_STREAM	Real-time video frame
AUDIO_STREAM	Audio packet
USER_JOIN / USER_LEAVE	Participant connection updates
UDP_REGISTER	Client’s UDP registration


##🧑‍💻 Example Network Setup
Device	Role	IP Address	Command
Laptop A	Server	192.168.1.10	python server3.py
Laptop B	Client	192.168.1.11	python clientv3.py 192.168.1.10 Alice
Laptop C	Client	192.168.1.12	python clientv3.py 192.168.1.10 Bob


##🧑‍🏫 Authors
Srihariram Asuvathraman — CS23B1063
Vijay Pranav — CS23B1073

## 🌟 Acknowledgments

**Indian Institute of Information Technology, Design and Manufacturing, Kancheepuram**  
**Course:** Computer Networks  
**Instructor:** Prof. Noor Mahammad  
**Academic Year:** 2025  

“This project demonstrates how computer network principles can be applied to design real-time communication and collaboration systems over LAN.”
