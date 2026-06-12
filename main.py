import webview
import threading
import time
import logging
from app import app, db, create_tables_and_users, normalise_roles, safe_migrate

# Initialize a logger to track raw physical device execution exceptions
logger = logging.getLogger("biztool_hardware")

create_tables_and_users()   # 1. create schema + seed admin
normalise_roles()           # 2. fix role casing
safe_migrate()              # 3. add new columns to existing DBs

def show_native_error(title, msg):
    print(f"{title}: {msg}")

# ══════════════════════════════════════════════════════════════
#  NATIVE HARDWARE BRIDGE INTERFACE FOR WEBVIEW CONTEXT
# ══════════════════════════════════════════════════════════════
class WebviewHardwareAPI:
    """
    Exposes native local hardware control features to the frontend
    JavaScript sandbox layer inside the pywebview render view window.
    """
    def printRawThermal(self, data_str):
        """
        Receives a raw string payload from the frontend, transforms it back 
        into an ESC/POS byte array, and writes it directly to the local printer spooler.
        """
        try:
            # Reconstruct the original raw ESC/POS binary byte array from the string stream
            raw_bytes = bytes([ord(c) for c in data_str])
            
            # Import standard Windows printing libraries dynamically at execution time
            import win32print
            
            # Fetch the active operating system default hardware device target name
            printer_name = win32print.GetDefaultPrinter()
            hPrinter = win32print.OpenPrinter(printer_name)
            
            try:
                # Open a raw, unmanaged print job sequence to feed raw ESC/POS bytes directly
                hJob = win32print.StartDocPrinter(hPrinter, 1, ("bizTOOL Thermal Receipt", None, "RAW"))
                win32print.StartPagePrinter(hPrinter)
                win32print.WritePrinter(hPrinter, raw_bytes)
                win32print.EndPagePrinter(hPrinter)
                win32print.EndDocPrinter(hPrinter)
                print(f"--- Thermal print job successfully sent to printer: {printer_name}")
            finally:
                win32print.ClosePrinter(hPrinter)
                
        except Exception as e:
            # Trace hardware errors gracefully without crashing the main application window
            print(f"Hardware Print Error: {e}")
            logger.error("Raw operating system printing link exception: %s", e)


def on_closing():
    """
    Intercepts the window close event from the desktop frame.
    Triggers a native Windows dialog box before termination.
    """
    # webview.windows[0] targets the first running window instance initialized by the app
    result = webview.windows[0].create_confirmation_dialog(
        'Exit BizTool POS', 
        'Are you sure you want to close the application?\nAny unsaved active transaction progress will be lost.'
    )
    if result:
        print("[INFO] Shutting down background server engines gracefully...")
        return True  # Allows pywebview to destroy the window and exit the main thread process
    return False  # Cancels window closure, keeping the app layout active


if __name__ == '__main__':
    try:
        # 1. DATABASE & AUDIT LOGIC
        with app.app_context():
            print("--- Initializing Database...")
            from models import Product, Receipt, User
            db.create_all()
            create_tables_and_users()
            
            # Price Audit
            print("--- Running Price Audit...")
            try:
                products_to_fix = Product.query.filter(
                    (Product.selling_price == None) | (Product.selling_price == 0)
                ).all()
                if products_to_fix:
                    for p in products_to_fix:
                        b_price = float(p.buying_price) if p.buying_price else 0.0
                        p.selling_price = round(b_price * 1.25, 2) if b_price > 0 else 10.0
                    db.session.commit()
            except Exception as e:
                print(f"Audit skipped: {e}")

        # 2. START FLASK
        def start_flask():
            # Use threaded=True to handle multiple local requests from the GUI
            app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False, threaded=True)

        flask_thread = threading.Thread(target=start_flask, daemon=True)
        flask_thread.start()

        # 3. GUI LAUNCH
        print("\n" + "="*50)
        print("LAUNCHING BIZTOOL POS")
        print("="*50 + "\n")
        
        # Give Flask time to bind to the port
        time.sleep(2)

        # Create hardware API bridge instance
        hardware_api = WebviewHardwareAPI()

        # Create window and attach the JS API bridge parameter
        window = webview.create_window(
            'BizTool POS', 
            'http://127.0.0.1:5000',
            width=1280, 
            height=800,
            js_api=hardware_api  # Binds the print Raw function directly to window.pywebview.api
        )
        
        # Attach the window closing hook mechanism
        window.events.closing += on_closing
        
        webview.start()

    except Exception as e:
        show_native_error("Fatal Error", str(e))