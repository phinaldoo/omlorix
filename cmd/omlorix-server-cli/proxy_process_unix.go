//go:build !windows

package main

import (
	"errors"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"strconv"
	"syscall"
)

// detachProxyProcess gives the managed proxy its own session so closing the
// terminal or Launcher window does not terminate public Omlorix access.
func detachProxyProcess(command *exec.Cmd) {
	command.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
}

func currentUserID() string { return strconv.Itoa(os.Getuid()) }

// Unix launchd/systemd services run as the same account that owns the runtime
// receipt, so the authenticated control endpoint may stop either a detached or
// user-service proxy without crossing a privilege boundary.
func proxyControlStopAllowed() bool { return true }

func superviseProxyServers(servers []*http.Server, listeners []net.Listener, config managedProxyConfig, stop <-chan struct{}) error {
	errorChannel := startProxyServers(servers, listeners, config)
	signalChannel := make(chan os.Signal, 1)
	signal.Notify(signalChannel, os.Interrupt, syscall.SIGTERM)
	defer signal.Stop(signalChannel)
	select {
	case <-stop:
		return nil
	case <-signalChannel:
		return nil
	case serveErr := <-errorChannel:
		if !errors.Is(serveErr, http.ErrServerClosed) {
			return serveErr
		}
		return nil
	}
}
