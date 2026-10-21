// pixelfm-nowplaying: a tiny bridge between PixelFM and macOS media controls.
//
// PixelFM (Python) starts this helper and talks to it with JSON lines:
//   stdin  <- {"type":"update","title":..,"artist":..,"album":..,"duration":s,
//             "elapsed":s,"playing":bool,"artwork":"/path/to/cover.jpg"}
//             {"type":"clear"}   {"type":"quit"}
//   stdout -> {"event":"ready"}  {"event":"command","name":"toggle"|"play"|
//             "pause"|"next"|"previous"|"seek","position":s}
//
// It publishes MPNowPlayingInfoCenter metadata (Control Centre, the menu bar
// Now Playing item, the lock screen) and receives MPRemoteCommandCenter
// commands (media keys, AirPods, Control Centre buttons). It never plays
// audio itself, keeps no state of its own beyond what PixelFM sends, and
// exits (clearing Now Playing) when PixelFM closes its stdin.

import AppKit
import Foundation
import MediaPlayer

setvbuf(stdout, nil, _IOLBF, 0)

func emit(_ object: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: object),
          let line = String(data: data, encoding: .utf8) else { return }
    print(line)
}

let center = MPNowPlayingInfoCenter.default()
let commands = MPRemoteCommandCenter.shared()
var artworkPath: String?
var artwork: MPMediaItemArtwork?

func clear() {
    center.nowPlayingInfo = nil
    center.playbackState = .stopped
}

func update(_ message: [String: Any]) {
    var info: [String: Any] = [:]
    if let title = message["title"] as? String { info[MPMediaItemPropertyTitle] = title }
    if let artist = message["artist"] as? String { info[MPMediaItemPropertyArtist] = artist }
    if let album = message["album"] as? String, !album.isEmpty {
        info[MPMediaItemPropertyAlbumTitle] = album
    }
    if let duration = message["duration"] as? Double, duration > 0 {
        info[MPMediaItemPropertyPlaybackDuration] = duration
    }
    let playing = (message["playing"] as? Bool) ?? false
    info[MPNowPlayingInfoPropertyElapsedPlaybackTime] = (message["elapsed"] as? Double) ?? 0
    info[MPNowPlayingInfoPropertyPlaybackRate] = playing ? 1.0 : 0.0
    info[MPNowPlayingInfoPropertyMediaType] = MPNowPlayingInfoMediaType.audio.rawValue
    let path = message["artwork"] as? String
    if path != artworkPath {
        artworkPath = path
        artwork = nil
        if let path, let image = NSImage(contentsOfFile: path) {
            artwork = MPMediaItemArtwork(boundsSize: image.size) { _ in image }
        }
    }
    if let artwork { info[MPMediaItemPropertyArtwork] = artwork }
    center.nowPlayingInfo = info
    center.playbackState = playing ? .playing : .paused
}

func handle(_ name: String) -> (MPRemoteCommandEvent) -> MPRemoteCommandHandlerStatus {
    return { _ in
        emit(["event": "command", "name": name])
        return .success
    }
}

commands.togglePlayPauseCommand.addTarget(handler: handle("toggle"))
commands.playCommand.addTarget(handler: handle("play"))
commands.pauseCommand.addTarget(handler: handle("pause"))
commands.nextTrackCommand.addTarget(handler: handle("next"))
commands.previousTrackCommand.addTarget(handler: handle("previous"))
commands.changePlaybackPositionCommand.addTarget { event in
    guard let event = event as? MPChangePlaybackPositionCommandEvent else {
        return .commandFailed
    }
    emit(["event": "command", "name": "seek", "position": event.positionTime])
    return .success
}
for command in [commands.togglePlayPauseCommand, commands.playCommand,
                commands.pauseCommand, commands.nextTrackCommand,
                commands.previousTrackCommand, commands.changePlaybackPositionCommand] {
    command.isEnabled = true
}

// Read PixelFM's messages off the main thread; apply them on it.
Thread.detachNewThread {
    while let line = readLine(strippingNewline: true) {
        guard let data = line.data(using: .utf8),
              let message = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let type = message["type"] as? String else { continue }
        DispatchQueue.main.async {
            switch type {
            case "update": update(message)
            case "clear": clear()
            case "quit": clear(); exit(0)
            default: break
            }
        }
    }
    // stdin closed: PixelFM has exited (or crashed). Leave nothing behind.
    DispatchQueue.main.async { clear(); exit(0) }
}

signal(SIGTERM) { _ in DispatchQueue.main.async { clear(); exit(0) } }
signal(SIGINT, SIG_IGN)  // Ctrl+C in the terminal is PixelFM's to handle

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)  // no Dock icon, no menu bar, no focus
emit(["event": "ready"])
app.run()
