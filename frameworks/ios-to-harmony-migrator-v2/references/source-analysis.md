# iOS 源工程分析

仅在新迁移、迁移切片或源行为存在歧义时读取。

新迁移的完整性和产物格式由 [discovery-gate.md](discovery-gate.md) 约束。本文件说明分析内容，不能替代门禁通过。

## 静态清点

- `.xcodeproj/.xcworkspace`、Target、Scheme、Configuration、最低系统版本、构建脚本。
- Objective-C、Swift、C/C++、UIKit、SwiftUI、Storyboard/XIB、生成代码和桥接头。
- CocoaPods、SPM、Carthage、二进制 Framework、系统 Framework、许可证和版本。
- App/Scene Delegate、根导航、URL/通知/后台入口、页面注册和弹层入口。
- 模型、服务、状态、缓存、数据库、Keychain、文件、Cookie 和升级逻辑。
- REST/WebSocket/GraphQL、鉴权、重试、取消、缓存、环境和错误分类。
- Info.plist、Entitlements、隐私清单、权限、Extension、Widget、App Group。
- 图片、字体、颜色、Lottie、音视频、本地化、无障碍标识和测试 Fixture。

## 行为提取

为迁移切片记录入口、主流程、失败/取消、重复点击、后台恢复、返回栈、持久化和异常状态。账号、会员、支付、权益和订单恢复单独建链路。

UI 页面只提取当前切片及共享组件的适用状态；全项目页面图在初始分析和 T4 校验。先使用源码和已有证据，只有歧义影响实现时才运行模拟器。

每个结论标为 `observed`、`inferred` 或 `unknown`，并引用文件、符号或运行证据。类名、扫描命中和截图外观本身不能证明行为。
