import AppKit
import Foundation

/// A state update received from the Electron launcher over standard input.
///
/// The native helper intentionally owns presentation only. Downloading,
/// signature verification, and installation remain in the launcher's updater
/// service, which sends small newline-delimited JSON state messages here.
private struct ProgressState: Decodable {
    let command: String?
    let phase: String?
    let windowTitle: String?
    let message: String?
    let detail: String?
    let percent: Double?
    let transferred: Double?
    let total: Double?
    let bytesPerSecond: Double?
    let primaryLabel: String?
    let secondaryLabel: String?
    let progressOf: String?
    let progressOfSpeed: String?
}

/// Runs the native macOS software-update window and bridges user actions back
/// to Electron over standard output.
final class UpdateProgressController: NSObject, NSApplicationDelegate, NSWindowDelegate, @unchecked Sendable {
    private let appIconPath: String
    private var window: NSWindow!
    private var messageLabel: NSTextField!
    private var detailLabel: NSTextField!
    private var progressIndicator: NSProgressIndicator!
    private var primaryButton: NSButton!
    private var secondaryButton: NSButton!
    private var currentPhase = "downloading"
    private var isClosingFromHost = false
    private var hasSentTerminalAction = false

    /// Creates the controller with the path used to obtain the launcher's real
    /// application icon from Launch Services.
    init(appIconPath: String) {
        self.appIconPath = appIconPath
        super.init()
    }

    /// Builds the window entirely from AppKit controls, starts listening for
    /// state messages, and brings the updater to the foreground.
    func applicationDidFinishLaunching(_ notification: Notification) {
        buildWindow()
        startReadingCommands()
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// Treats closing the window as Cancel unless Electron requested the close
    /// after completing or deferring the update flow.
    func windowWillClose(_ notification: Notification) {
        if !isClosingFromHost {
            sendAction("cancel")
        }
        NSApp.terminate(nil)
    }

    /// Constructs the software-update layout with native macOS controls and
    /// Auto Layout. No HTML, CSS, or web-rendered widget is used here.
    private func buildWindow() {
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 560, height: 190),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        window.title = "Omlorix Server Launcher"
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.standardWindowButton(.miniaturizeButton)?.isEnabled = false
        window.standardWindowButton(.zoomButton)?.isEnabled = false

        let rootView = NSView()
        rootView.translatesAutoresizingMaskIntoConstraints = false
        window.contentView = rootView

        let iconView = NSImageView()
        iconView.translatesAutoresizingMaskIntoConstraints = false
        iconView.image = applicationIcon()
        iconView.imageScaling = .scaleProportionallyUpOrDown
        iconView.setAccessibilityElement(false)
        iconView.setContentHuggingPriority(.required, for: .horizontal)

        messageLabel = NSTextField(labelWithString: "")
        messageLabel.translatesAutoresizingMaskIntoConstraints = false
        messageLabel.font = .systemFont(ofSize: 18, weight: .semibold)
        messageLabel.lineBreakMode = .byTruncatingTail

        progressIndicator = NSProgressIndicator()
        progressIndicator.translatesAutoresizingMaskIntoConstraints = false
        progressIndicator.style = .bar
        progressIndicator.minValue = 0
        progressIndicator.maxValue = 100
        progressIndicator.doubleValue = 0
        progressIndicator.isIndeterminate = false

        detailLabel = NSTextField(labelWithString: "")
        detailLabel.translatesAutoresizingMaskIntoConstraints = false
        detailLabel.font = .systemFont(ofSize: 14, weight: .regular)
        detailLabel.textColor = .secondaryLabelColor
        detailLabel.lineBreakMode = .byTruncatingTail
        detailLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)

        secondaryButton = makeButton(title: "", action: #selector(secondaryPressed))
        secondaryButton.isHidden = true

        primaryButton = makeButton(title: "", action: #selector(primaryPressed))
        primaryButton.isHidden = true
        primaryButton.keyEquivalent = "\r"

        let buttonStack = NSStackView(views: [secondaryButton, primaryButton])
        buttonStack.translatesAutoresizingMaskIntoConstraints = false
        buttonStack.orientation = .horizontal
        buttonStack.alignment = .centerY
        buttonStack.spacing = 8
        buttonStack.setContentHuggingPriority(.required, for: .horizontal)

        // Use explicit edge constraints instead of a horizontal stack for the
        // status row. This keeps actions pinned to the far trailing edge even
        // when the localized byte-count text changes length.
        let bottomRow = NSView()
        bottomRow.translatesAutoresizingMaskIntoConstraints = false
        bottomRow.addSubview(detailLabel)
        bottomRow.addSubview(buttonStack)

        let contentStack = NSStackView(views: [messageLabel, progressIndicator, bottomRow])
        contentStack.translatesAutoresizingMaskIntoConstraints = false
        contentStack.orientation = .vertical
        contentStack.alignment = .leading
        contentStack.spacing = 14

        rootView.addSubview(iconView)
        rootView.addSubview(contentStack)

        NSLayoutConstraint.activate([
            iconView.leadingAnchor.constraint(equalTo: rootView.leadingAnchor, constant: 30),
            iconView.centerYAnchor.constraint(equalTo: rootView.centerYAnchor),
            iconView.widthAnchor.constraint(equalToConstant: 68),
            iconView.heightAnchor.constraint(equalToConstant: 68),

            contentStack.leadingAnchor.constraint(equalTo: iconView.trailingAnchor, constant: 24),
            contentStack.trailingAnchor.constraint(equalTo: rootView.trailingAnchor, constant: -30),
            contentStack.centerYAnchor.constraint(equalTo: rootView.centerYAnchor),

            messageLabel.widthAnchor.constraint(equalTo: contentStack.widthAnchor),
            progressIndicator.widthAnchor.constraint(equalTo: contentStack.widthAnchor),
            progressIndicator.heightAnchor.constraint(equalToConstant: 10),
            bottomRow.widthAnchor.constraint(equalTo: contentStack.widthAnchor),
            detailLabel.leadingAnchor.constraint(equalTo: bottomRow.leadingAnchor),
            detailLabel.centerYAnchor.constraint(equalTo: bottomRow.centerYAnchor),
            detailLabel.trailingAnchor.constraint(lessThanOrEqualTo: buttonStack.leadingAnchor, constant: -14),
            buttonStack.topAnchor.constraint(equalTo: bottomRow.topAnchor),
            buttonStack.trailingAnchor.constraint(equalTo: bottomRow.trailingAnchor),
            buttonStack.bottomAnchor.constraint(equalTo: bottomRow.bottomAnchor),
            primaryButton.widthAnchor.constraint(greaterThanOrEqualToConstant: 108),
            secondaryButton.widthAnchor.constraint(greaterThanOrEqualToConstant: 88),
        ])
    }

    /// Creates a standard rounded AppKit button with a launcher action.
    private func makeButton(title: String, action: Selector) -> NSButton {
        let button = NSButton(title: title, target: self, action: action)
        button.translatesAutoresizingMaskIntoConstraints = false
        button.bezelStyle = .rounded
        button.controlSize = .regular
        button.font = .systemFont(ofSize: 14, weight: .regular)
        return button
    }

    /// Resolves the real launcher icon. Packaged builds pass their `.app`
    /// bundle; development builds pass the checked-in icon asset directly.
    private func applicationIcon() -> NSImage {
        if !appIconPath.isEmpty {
            if appIconPath.hasSuffix(".app") {
                return NSWorkspace.shared.icon(forFile: appIconPath)
            }
            if let image = NSImage(contentsOfFile: appIconPath) {
                return image
            }
        }
        return NSImage(named: NSImage.applicationIconName) ?? NSImage(size: NSSize(width: 68, height: 68))
    }

    /// Continuously decodes newline-delimited JSON without blocking AppKit's
    /// main event loop. UI changes are always applied back on the main queue.
    private func startReadingCommands() {
        Thread.detachNewThread { [weak self] in
            while let line = readLine(strippingNewline: true) {
                guard let data = line.data(using: .utf8) else { continue }
                do {
                    let state = try JSONDecoder().decode(ProgressState.self, from: data)
                    DispatchQueue.main.async {
                        self?.apply(state)
                    }
                } catch {
                    FileHandle.standardError.write(
                        Data("[native-update-ui] Invalid state message: \(error)\n".utf8)
                    )
                }
            }
            DispatchQueue.main.async {
                self?.closeAfterHostInputEnded()
            }
        }
    }

    /// Prevents an orphaned updater window if the Electron host exits without
    /// sending the normal close command.
    private func closeAfterHostInputEnded() {
        guard !isClosingFromHost else { return }
        isClosingFromHost = true
        window.close()
    }

    /// Applies one launcher state update to the native controls.
    private func apply(_ state: ProgressState) {
        if state.command == "close" {
            isClosingFromHost = true
            window.close()
            return
        }

        currentPhase = state.phase ?? currentPhase
        if let windowTitle = state.windowTitle, !windowTitle.isEmpty {
            window.title = windowTitle
        }
        messageLabel.stringValue = state.message ?? ""
        detailLabel.stringValue = progressDetail(for: state)

        let shouldAnimate = currentPhase == "checking" || currentPhase == "finishing"
        progressIndicator.isIndeterminate = shouldAnimate
        if shouldAnimate {
            progressIndicator.startAnimation(nil)
        } else {
            progressIndicator.stopAnimation(nil)
            progressIndicator.doubleValue = min(100, max(0, state.percent ?? 0))
        }

        configure(primaryButton, title: state.primaryLabel)
        configure(secondaryButton, title: state.secondaryLabel)
        primaryButton.keyEquivalent = currentPhase == "ready" || currentPhase == "available" ? "\r" : ""
    }

    /// Updates a button while preserving native button metrics and focus rings.
    private func configure(_ button: NSButton, title: String?) {
        let normalizedTitle = title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        button.title = normalizedTitle
        button.isHidden = normalizedTitle.isEmpty
    }

    /// Formats transfer progress with Foundation's locale-aware byte formatter,
    /// matching the native updater convention shown by macOS applications.
    private func progressDetail(for state: ProgressState) -> String {
        if currentPhase == "downloading", let transferred = state.transferred, let total = state.total, total > 0 {
            let transferredText = ByteCountFormatter.string(fromByteCount: Int64(transferred), countStyle: .file)
            let totalText = ByteCountFormatter.string(fromByteCount: Int64(total), countStyle: .file)
            if let speed = state.bytesPerSecond, speed > 0 {
                let speedText = ByteCountFormatter.string(fromByteCount: Int64(speed), countStyle: .file)
                let template = state.progressOfSpeed ?? "{transferred} / {total} — {speed}/s"
                return interpolateProgress(
                    template,
                    transferred: transferredText,
                    total: totalText,
                    speed: speedText
                )
            }
            let template = state.progressOf ?? "{transferred} / {total}"
            return interpolateProgress(template, transferred: transferredText, total: totalText, speed: "")
        }
        return state.detail ?? ""
    }

    /// Applies the named placeholders supplied by the Electron translation
    /// catalog without making the native helper responsible for any locale.
    private func interpolateProgress(
        _ template: String,
        transferred: String,
        total: String,
        speed: String
    ) -> String {
        return template
            .replacingOccurrences(of: "{transferred}", with: transferred)
            .replacingOccurrences(of: "{total}", with: total)
            .replacingOccurrences(of: "{speed}", with: speed)
    }

    /// Maps the primary native button to the action expected by the launcher's
    /// existing update state machine.
    @objc private func primaryPressed() {
        if currentPhase == "checking" || currentPhase == "downloading" {
            sendAction("cancel")
        } else {
            sendAction("primary")
        }
    }

    /// Sends the secondary action used by the Later button.
    @objc private func secondaryPressed() {
        sendAction("secondary")
    }

    /// Writes one action as newline-delimited JSON. Standard output is reserved
    /// exclusively for this protocol so Electron can parse it safely.
    private func sendAction(_ action: String) {
        guard !hasSentTerminalAction else { return }
        hasSentTerminalAction = true
        guard let data = try? JSONSerialization.data(withJSONObject: ["action": action]) else { return }
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    }
}

/// Returns the value following a named command-line argument.
private func argumentValue(_ name: String) -> String {
    guard let index = CommandLine.arguments.firstIndex(of: name) else { return "" }
    let valueIndex = CommandLine.arguments.index(after: index)
    guard valueIndex < CommandLine.arguments.endIndex else { return "" }
    return CommandLine.arguments[valueIndex]
}

let application = NSApplication.shared
let controller = UpdateProgressController(appIconPath: argumentValue("--icon-path"))
application.setActivationPolicy(.accessory)
application.delegate = controller
application.run()
