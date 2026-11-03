@Entry
@Component
struct Index {
  @State private title: string = '示例功能'

  build() {
    Column() {
      Text(this.title).fontColor('#999999')
    }
  }
}
