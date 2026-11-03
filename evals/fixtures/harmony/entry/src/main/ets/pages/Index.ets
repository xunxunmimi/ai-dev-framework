@Entry
@Component
struct Index {
  @State private title: string | undefined = undefined
  @State private count: number = 0

  build() {
    Column() {
      Text(this.title!)
        .fontColor('#999999')
      Button('增加')
        .onClick(() => this.count += 1)
    }
  }
}
