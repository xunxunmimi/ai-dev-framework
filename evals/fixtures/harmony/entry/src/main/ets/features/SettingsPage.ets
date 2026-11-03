@Component
export struct SettingsPage {
  @State private enabled: boolean = false

  build() {
    Toggle({ type: ToggleType.Switch, isOn: this.enabled })
      .onChange((value: boolean) => {
        AppStorage.setOrCreate('notificationsEnabled', value)
      })
  }
}
