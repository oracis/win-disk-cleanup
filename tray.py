# -*- coding: utf-8 -*-
"""
零依赖系统托盘（仅 ctypes，Windows only）。

创建一个隐藏窗口 + NOTIFYICON，右键菜单：打开界面 / 以管理员重启 / 退出。
消息循环跑在独立线程里。任何一步失败都抛异常，由 app.py 降级为「无托盘」。

API:
    t = tray.create(url, on_open=..., on_exit=..., on_admin=...)
    threading.Thread(target=t.run, daemon=True).start()   # 阻塞在线程里
    t.stop()                                              # 让线程退出
"""
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

# ---- Windows 类型（自管别名，避免 ctypes.wintypes 缺项） ----
PVOID = ctypes.c_void_p
HWND = PVOID
HINSTANCE = PVOID
HICON = PVOID
HCURSOR = PVOID
HBRUSH = PVOID
HMENU = PVOID
LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_void_p
LPARAM = ctypes.c_ssize_t
UINT = ctypes.c_uint
DWORD = ctypes.c_uint
WCHAR = ctypes.c_wchar
LPCWSTR = ctypes.c_wchar_p

# ---- 常量 ----
WM_USER = 0x400
WM_TRAY = WM_USER + 1
WM_COMMAND = 0x0111
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202
WM_DESTROY = 0x0002

NIM_ADD = 0x0
NIM_DELETE = 0x2
NIF_MESSAGE = 0x1
NIF_ICON = 0x2
NIF_TIP = 0x4

TPM_RETURNCMD = 0x100

ID_OPEN = 1001
ID_ADMIN = 1002
ID_EXIT = 1003

IDI_APPLICATION = 32512
IDC_ARROW = 32512


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", DWORD),
        ("hWnd", HWND),
        ("uID", UINT),
        ("uFlags", UINT),
        ("uCallbackMessage", UINT),
        ("hIcon", HICON),
        ("szTip", WCHAR * 128),
        ("dwState", DWORD),
        ("dwStateMask", DWORD),
        ("szInfo", WCHAR * 256),
        ("uVersion", UINT),
        ("szInfoTitle", WCHAR * 64),
        ("dwInfoFlags", DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", HICON),
    ]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", HINSTANCE),
        ("hIcon", HICON),
        ("hCursor", HCURSOR),
        ("hbrBackground", HBRUSH),
        ("lpszMenuName", LPCWSTR),
        ("lpszClassName", LPCWSTR),
    ]


# ---- 函数绑定（64 位必须显式类型，否则句柄按 c_int 溢出） ----
kernel32.GetModuleHandleW.restype = HINSTANCE
kernel32.GetModuleHandleW.argtypes = [LPCWSTR]

user32.RegisterClassW.restype = ctypes.c_uint
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]

user32.CreateWindowExW.restype = HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, LPCWSTR, LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    HWND, HMENU, HINSTANCE, wintypes.LPVOID]

user32.DestroyWindow.restype = ctypes.c_int
user32.DestroyWindow.argtypes = [HWND]

user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [HWND, UINT, WPARAM, LPARAM]

user32.LoadIconW.restype = HICON
user32.LoadIconW.argtypes = [HINSTANCE, UINT]
user32.LoadCursorW.restype = HCURSOR
user32.LoadCursorW.argtypes = [HINSTANCE, UINT]

user32.SetForegroundWindow.restype = ctypes.c_int
user32.SetForegroundWindow.argtypes = [HWND]

user32.PostMessageW.restype = ctypes.c_int
user32.PostMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]

user32.PostQuitMessage.restype = None
user32.PostQuitMessage.argtypes = [ctypes.c_int]

user32.CreatePopupMenu.restype = HMENU
user32.CreatePopupMenu.argtypes = []
user32.DestroyMenu.restype = ctypes.c_int
user32.DestroyMenu.argtypes = [HMENU]
user32.AppendMenuW.restype = ctypes.c_int
user32.AppendMenuW.argtypes = [HMENU, UINT, ctypes.c_void_p, LPCWSTR]
user32.TrackPopupMenu.restype = ctypes.c_uint
user32.TrackPopupMenu.argtypes = [HMENU, UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, HWND, HMENU]

user32.GetCursorPos.restype = ctypes.c_int
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]

user32.TranslateMessage.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.GetMessageW.restype = ctypes.c_int
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), HWND, UINT, UINT]

shell32.Shell_NotifyIconW.restype = ctypes.c_int
shell32.Shell_NotifyIconW.argtypes = [UINT, ctypes.POINTER(NOTIFYICONDATA)]


class TrayIcon:
    def __init__(self, url, on_open, on_exit, on_admin):
        self.url = url
        self.on_open = on_open
        self.on_exit = on_exit
        self.on_admin = on_admin
        self.hwnd = None
        self.hicon = None
        self.atom = None
        self.wc = None
        self.wndproc = None
        self._nid = None

    def run(self):
        self.wndproc = WNDPROC(self._wndproc)
        self.wc = WNDCLASS()
        self.wc.style = 0
        self.wc.lpfnWndProc = self.wndproc
        self.wc.hInstance = kernel32.GetModuleHandleW(None)
        self.wc.hIcon = user32.LoadIconW(0, IDI_APPLICATION)
        self.wc.hCursor = user32.LoadCursorW(0, IDC_ARROW)
        self.wc.lpszClassName = "WinDiskCleanupTray"
        self.atom = user32.RegisterClassW(ctypes.byref(self.wc))
        if not self.atom:
            raise ctypes.WinError(ctypes.get_last_error(), "RegisterClassW failed")

        self.hwnd = user32.CreateWindowExW(
            0, self.wc.lpszClassName, "WDC", 0, 0, 0, 0, 0, 0, 0,
            self.wc.hInstance, 0)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error(), "CreateWindowExW failed")

        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self.wc.hIcon
        nid.szTip = "Win Disk Cleanup 磁盘清理"
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            raise ctypes.WinError(ctypes.get_last_error(), "Shell_NotifyIconW failed")
        self._nid = nid

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            if lparam in (WM_RBUTTONUP, WM_LBUTTONUP):
                self._show_menu()
            return 0
        if msg == WM_COMMAND:
            cmd = wparam & 0xFFFF
            if cmd == ID_OPEN and self.on_open:
                self.on_open()
            elif cmd == ID_ADMIN and self.on_admin:
                self.on_admin()
            elif cmd == ID_EXIT:
                self._quit()
            return 0
        if msg == WM_DESTROY:
            self._quit()
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self):
        hmenu = user32.CreatePopupMenu()
        user32.AppendMenuW(hmenu, 0, ID_OPEN, "打开界面")
        user32.AppendMenuW(hmenu, 0, ID_ADMIN, "以管理员重启")
        user32.AppendMenuW(hmenu, 0, ID_EXIT, "退出")
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(hmenu, TPM_RETURNCMD, pt.x, pt.y,
                                    0, self.hwnd, 0)
        user32.DestroyMenu(hmenu)
        if cmd == ID_OPEN and self.on_open:
            self.on_open()
        elif cmd == ID_ADMIN and self.on_admin:
            self.on_admin()
        elif cmd == ID_EXIT:
            self._quit()
        # 释放 SetForegroundWindow 后的锁定
        user32.PostMessageW(self.hwnd, 0, 0, 0)

    def _quit(self):
        try:
            if self._nid:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
        except Exception:
            pass
        user32.PostQuitMessage(0)

    def stop(self):
        try:
            if self.hwnd:
                user32.PostMessageW(self.hwnd, WM_DESTROY, 0, 0)
        except Exception:
            pass


def create(url, on_open=None, on_exit=None, on_admin=None):
    return TrayIcon(url, on_open, on_exit, on_admin)
