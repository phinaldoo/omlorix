//go:build windows

package main

import (
	"errors"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"syscall"

	"golang.org/x/sys/windows/svc"
)

// CREATE_NEW_PROCESS_GROUP separates the proxy from the calling console while
// lifecycle commands authenticate to its loopback control endpoint.
func detachProxyProcess(command *exec.Cmd) {
	command.SysProcAttr = &syscall.SysProcAttr{CreationFlags: syscall.CREATE_NEW_PROCESS_GROUP}
}

func currentUserID() string { return "current" }

type windowsProxyService struct {
	servers   []*http.Server
	listeners []net.Listener
	config    managedProxyConfig
	stop      <-chan struct{}
}

func (service *windowsProxyService) Execute(
	_ []string,
	requests <-chan svc.ChangeRequest,
	statuses chan<- svc.Status,
) (bool, uint32) {
	const accepted = svc.AcceptStop | svc.AcceptShutdown
	statuses <- svc.Status{State: svc.StartPending}
	errorChannel := startProxyServers(service.servers, service.listeners, service.config)
	statuses <- svc.Status{State: svc.Running, Accepts: accepted}
	for {
		select {
		case <-service.stop:
			statuses <- svc.Status{State: svc.StopPending}
			_ = shutdownProxyServers(service.servers)
			return false, 0
		case request := <-requests:
			switch request.Cmd {
			case svc.Interrogate:
				statuses <- request.CurrentStatus
			case svc.Stop, svc.Shutdown:
				statuses <- svc.Status{State: svc.StopPending}
				_ = shutdownProxyServers(service.servers)
				return false, 0
			}
		case serveErr := <-errorChannel:
			if !errors.Is(serveErr, http.ErrServerClosed) {
				_ = shutdownProxyServers(service.servers)
				return true, 1
			}
			return false, 0
		}
	}
}

func proxyControlStopAllowed() bool {
	isService, err := svc.IsWindowsService()
	return err == nil && !isService
}

func superviseProxyServers(servers []*http.Server, listeners []net.Listener, config managedProxyConfig, stop <-chan struct{}) error {
	isService, err := svc.IsWindowsService()
	if err != nil {
		return err
	}
	if isService {
		return svc.Run("OmlorixServerProxy", &windowsProxyService{servers: servers, listeners: listeners, config: config, stop: stop})
	}
	errorChannel := startProxyServers(servers, listeners, config)
	signalChannel := make(chan os.Signal, 1)
	signal.Notify(signalChannel, os.Interrupt)
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
