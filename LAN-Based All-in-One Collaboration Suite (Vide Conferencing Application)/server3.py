import socket
import threading
import struct
import time
import json
from enum import Enum

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
    UDP_REGISTER = 12  # NEW: Client registers UDP port

HOST = '0.0.0.0'
TCP_PORT = 5000
UDP_PORT = 5001
BUFFER_SIZE = 65536  # Increased for images

class Server:
    def __init__(self):
        self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.tcp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp_socket.bind((HOST, TCP_PORT))
        self.udp_socket.bind((HOST, UDP_PORT))
        self.tcp_socket.listen(5)
        
        # Store client info: {username: {'tcp': sock, 'udp_addr': (ip, port), 'tcp_addr': addr}}
        self.clients = {}
        self.clients_lock = threading.Lock()
        
        print(f"Server listening on TCP:{TCP_PORT}, UDP:{UDP_PORT}")

    def start(self):
        threading.Thread(target=self.handle_tcp_connections, daemon=True).start()
        threading.Thread(target=self.handle_udp_packets, daemon=True).start()
        
        print("Server started. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down server...")

    def handle_tcp_connections(self):
        while True:
            try:
                client_sock, addr = self.tcp_socket.accept()
                threading.Thread(target=self.handle_client_tcp, args=(client_sock, addr), daemon=True).start()
            except Exception as e:
                print(f"TCP accept error: {e}")

    def handle_client_tcp(self, client_sock, addr):
        username = None
        try:
            # Receive username
            username = client_sock.recv(1024).decode().strip()
            if not username:
                return
            
            # Make username unique
            original_username = username
            counter = 1
            with self.clients_lock:
                while username in self.clients:
                    username = f"{original_username}_{counter}"
                    counter += 1
                
                self.clients[username] = {
                    'tcp': client_sock,
                    'udp_addr': None,
                    'tcp_addr': addr
                }
            
            print(f"{username} connected from {addr}")
            
            # Send confirmation back
            client_sock.send(username.encode())
            
            # Notify all other clients
            self.broadcast_tcp(MessageType.USER_JOIN, {"user": username}, exclude_username=username)
            
            # Main message loop
            buffer = b''
            while True:
                data = client_sock.recv(BUFFER_SIZE)
                if not data:
                    break
                
                buffer += data
                
                # Process complete messages
                while len(buffer) >= 5:
                    msg_type_byte = buffer[0]
                    length = struct.unpack('!I', buffer[1:5])[0]
                    
                    if len(buffer) < 5 + length:
                        break  # Wait for more data
                    
                    msg_type = MessageType(msg_type_byte)
                    payload_bytes = buffer[5:5+length]
                    buffer = buffer[5+length:]
                    
                    try:
                        payload = json.loads(payload_bytes.decode())
                        self.handle_tcp_message(msg_type, payload, username)
                    except Exception as e:
                        print(f"Error processing message from {username}: {e}")
                        
        except Exception as e:
            print(f"Error with client {username}: {e}")
        finally:
            self.handle_disconnect(username)

    def handle_tcp_message(self, msg_type, payload, username):
        if msg_type == MessageType.CHAT:
            # Add username and broadcast
            payload['user'] = username
            self.broadcast_tcp(msg_type, payload)
            print(f"[CHAT] {username}: {payload.get('msg', '')}")
            
        elif msg_type == MessageType.UDP_REGISTER:
            # Client sending their UDP port
            udp_port = payload.get('port')
            with self.clients_lock:
                if username in self.clients:
                    client_ip = self.clients[username]['tcp_addr'][0]
                    self.clients[username]['udp_addr'] = (client_ip, udp_port)
                    print(f"{username} registered UDP at {client_ip}:{udp_port}")
                    
        elif msg_type == MessageType.FILE_NOTIFY:
            payload['user'] = username
            self.broadcast_tcp(msg_type, payload, exclude_username=username)
            print(f"[FILE] {username} sharing: {payload.get('filename')}")
            
        elif msg_type == MessageType.FILE_CHUNK:
            # Relay file chunks
            payload['user'] = username
            self.broadcast_tcp(msg_type, payload, exclude_username=username)
            
        elif msg_type == MessageType.SCREEN_START:
            payload['user'] = username
            self.broadcast_tcp(msg_type, payload, exclude_username=username)
            print(f"[SCREEN] {username} started screen share")
            
        elif msg_type == MessageType.SCREEN_IMAGE:
            payload['user'] = username
            self.broadcast_tcp(msg_type, payload, exclude_username=username)
            
        elif msg_type == MessageType.SCREEN_STOP:
            payload['user'] = username
            self.broadcast_tcp(msg_type, payload, exclude_username=username)
            print(f"[SCREEN] {username} stopped screen share")

    def handle_udp_packets(self):
        while True:
            try:
                data, addr = self.udp_socket.recvfrom(BUFFER_SIZE)
                
                if len(data) < 5:
                    continue
                
                msg_type = MessageType(data[0])
                length = struct.unpack('!I', data[1:5])[0]
                payload = data[5:5+length]
                
                # Find sender by UDP address
                sender_username = None
                with self.clients_lock:
                    for username, info in self.clients.items():
                        if info['udp_addr'] == addr:
                            sender_username = username
                            break
                
                if not sender_username:
                    continue
                
                # Broadcast to all other clients
                if msg_type in (MessageType.VIDEO_STREAM, MessageType.AUDIO_STREAM):
                    # Add username to payload for identification
                    username_bytes = sender_username.encode()
                    new_payload = struct.pack('!I', len(username_bytes)) + username_bytes + payload
                    new_data = struct.pack('!BI', msg_type.value, len(new_payload)) + new_payload
                    
                    with self.clients_lock:
                        for username, info in self.clients.items():
                            if username != sender_username and info['udp_addr']:
                                try:
                                    self.udp_socket.sendto(new_data, info['udp_addr'])
                                except Exception as e:
                                    print(f"UDP send error to {username}: {e}")
                                    
            except Exception as e:
                print(f"UDP error: {e}")

    def handle_disconnect(self, username):
        if username:
            with self.clients_lock:
                if username in self.clients:
                    try:
                        self.clients[username]['tcp'].close()
                    except:
                        pass
                    del self.clients[username]
            
            print(f"{username} disconnected")
            self.broadcast_tcp(MessageType.USER_LEAVE, {"user": username})

    def broadcast_tcp(self, msg_type, payload, exclude_username=None):
        data = self.pack_message(msg_type, payload)
        
        with self.clients_lock:
            for username, info in list(self.clients.items()):
                if username != exclude_username:
                    try:
                        info['tcp'].send(data)
                    except Exception as e:
                        print(f"Failed to send to {username}: {e}")

    def pack_message(self, msg_type, payload):
        payload_bytes = json.dumps(payload).encode()
        length = len(payload_bytes)
        return struct.pack('!BI', msg_type.value, length) + payload_bytes

if __name__ == "__main__":
    server = Server()
    server.start()