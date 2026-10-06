"""Explicit pytest plugin: deny TCP/DNS, including errors caught by application code."""

import socket
import sys
from collections.abc import Callable

_SOCKETPAIR_CODE = getattr(socket, "_fallback_socketpair", lambda: None).__code__
_DNS_FUNCTIONS = ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo")


def _stdlib_socketpair(client, args, kwargs, wrapper_code) -> bool:
    """Allow only stdlib's authenticated local socket pair, not general loopback."""
    caller = sys._getframe(2)
    # Nested guards retain exactly the same boundary without a global bypass flag.
    while caller is not None and caller.f_code is wrapper_code:
        caller = caller.f_back
    if caller is None or caller.f_code is not _SOCKETPAIR_CODE or kwargs:
        return False
    locals_ = caller.f_locals
    listener = locals_.get("lsock")
    if locals_.get("csock") is not client or not isinstance(listener, socket.socket):
        return False
    address = (locals_.get("addr"), locals_.get("port"))
    return (
        args == (address,)
        and address[0] in {"127.0.0.1", "::1"}
        and listener.getsockname()[:2] == address
    )


class NetworkGuard:
    def __init__(self) -> None:
        self.violations = 0
        self._originals: dict[str, Callable] | None = None

    def _deny(self, *args, **kwargs):
        self.violations += 1
        raise RuntimeError("offline_network_forbidden")

    def install(self) -> None:
        if self._originals is not None:
            return
        self._originals = {"connect": socket.socket.connect, "connect_ex": socket.socket.connect_ex,
                           **{name: getattr(socket, name) for name in _DNS_FUNCTIONS}}

        def wrap(original):
            def guarded(client, *args, **kwargs):
                # Local Unix-domain IPC used by event loops is not TCP/network.
                if client.family not in {socket.AF_INET, socket.AF_INET6}:
                    return original(client, *args, **kwargs)
                if _stdlib_socketpair(client, args, kwargs, guarded.__code__):
                    return original(client, *args, **kwargs)
                return self._deny()
            return guarded

        socket.socket.connect = wrap(self._originals["connect"])
        socket.socket.connect_ex = wrap(self._originals["connect_ex"])
        for name in _DNS_FUNCTIONS:
            setattr(socket, name, self._deny)

    def restore(self) -> None:
        if self._originals is not None:
            socket.socket.connect = self._originals["connect"]
            socket.socket.connect_ex = self._originals["connect_ex"]
            for name in _DNS_FUNCTIONS:
                setattr(socket, name, self._originals[name])
            self._originals = None


def pytest_configure(config) -> None:
    guard = NetworkGuard()
    guard.install()
    config._docucheck_network_guard = guard


def pytest_sessionfinish(session, exitstatus) -> None:
    guard = session.config._docucheck_network_guard
    if guard.violations:
        session.exitstatus = 1
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter:
        # ensure_newline() only handles a current file label, not pytest's progress line.
        reporter.write_line("")
        reporter.write_line(f"offline_network_attempts={guard.violations}")


def pytest_unconfigure(config) -> None:
    guard = getattr(config, "_docucheck_network_guard", None)
    if guard is not None:
        guard.restore()
