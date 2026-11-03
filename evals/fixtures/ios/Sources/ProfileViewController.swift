import Foundation

final class ProfileViewController {
    var displayName: String?

    /// 返回帐号名称；名称为空时当前实现会崩溃。
    func titleText() -> String {
        displayName!
    }
}
