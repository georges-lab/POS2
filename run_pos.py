import webview
import threading
from app import app
import time

def start_server():
    # Set host to 0.0.0.0 so the internal bridge works
    app.run(host='127.0.0.1', port=5000, debug=False)

if __name__ == '__main__':
    # 1. Start Flask in a background thread
    t = threading.Thread(target=start_server)
    t.daemon = True
    t.start()
    
    # Give the server a second to boot up
    time.sleep(2)
    
    # 2. Launch the GUI Window
    webview.create_window('BizTool POS', 'http://127.0.0.1:5000', width=1200, height=800)
    webview.start()