@Entry
@Component
struct Index {
  @State private title: string = '格式转换'

  build() {
    Column() {
      Text(this.title).fontColor('#999999')
    }
  }
}
