import Foundation

final class SettingsStore {
    private(set) var notificationsEnabled = false

    func saveNotificationsEnabled(_ enabled: Bool) {
        UserDefaults.standard.set(enabled, forKey: "notificationsEnabled")
    }
}
