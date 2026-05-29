import http.server
import subprocess
import urllib.parse
import sys

class NotificationHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress standard logging to keep console output clean
        return

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        msg = params.get('msg', [''])[0]
        title = params.get('title', ['🚴 Bikesensor Sync'])[0]
        sound = params.get('sound', ['Glass'])[0]
        
        if msg:
            # Escape double quotes in the message
            escaped_msg = msg.replace('"', '\\"')
            escaped_title = title.replace('"', '\\"')
            cmd = f'osascript -e \'display notification "{escaped_msg}" with title "{escaped_title}" sound name "{sound}"\''
            subprocess.run(cmd, shell=True)
            
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"OK")

if __name__ == '__main__':
    port = 8089
    server = http.server.HTTPServer(('0.0.0.0', port), NotificationHandler)
    print(f"Bikesensor macOS GUI Notification Listener started on port {port}...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping listener...")
        sys.exit(0)
