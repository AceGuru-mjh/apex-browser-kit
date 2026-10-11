# apex-browser-kit

Embedded WebView automation engine with Compose chrome UI — 从
[Android-Guru-Agent](https://github.com/Ultra-Guru/Android-Guru-Agent) 拆出的浏览器能力库。

[![CI](https://github.com/Ultra-Guru/apex-browser-kit/actions/workflows/ci.yml/badge.svg)](https://github.com/Ultra-Guru/apex-browser-kit/actions/workflows/ci.yml)
![Consumer](https://img.shields.io/badge/consumer-Android--Guru--Agent-blue)

## 模块结构

| 模块 | 类型 | 职责 |
|---|---|---|
| `:browser-core` | 纯 Kotlin JVM（零 Android 依赖） | DOM 模型（`DomElement`/`PageSnapshot`）、注入 JS（`BrowserScript`）、快照解析（`DomParser`）、重试/熔断（`RetryPolicy`/`CircuitBreaker`） |
| `:browser-engine` | Android Library（无 UI） | 无头 WebView 自动化引擎（`BrowserEngine`）：状态机、标签管理、物理触摸注入、加载等待、Cookie 持久化、崩溃恢复 |
| `:browser-chrome` | Android Library（Compose UI） | 「给人用的浏览器界面」：地址胶囊即进度条、标签条/总览、页内查找、下载面板、JS 弹窗/权限浮层、浮窗宿主（`BrowserOverlay`） |

依赖方向：`chrome → engine → core`（`api` 传递，下游引 chrome 即得全套类型）。

## 安全：JS 注入边界（`JsLiteral`）

浏览器 Agent 天生暴露在**间接提示注入**下：网页正文可能被模型当作「元素 ref」原样回传。
因此本库把「外部字符串 → JS 字面量」的编码收敛到唯一入口
**`JsLiteral.string`**（`browser-core/.../core/JsLiteral.kt`），覆盖 `'` `\`、
换行/回车/制表等控制字符，以及 U+2028 / U+2029（JSON 合法但 JS 字面量非法）。

> **v1.0.1 修复（已随 rebase 并入 1.1.0 发布列车）**：v1.0.0 的注入点是 `private fun String.toJsonString() = "'$this'"`，
> 零转义。实测 `ref = "+alert(document.cookie)+"` 可生成
> `document.querySelector('[data-apex-hash='+alert(document.cookie)+']')`
> —— **由 ref 字符串驱动的页内任意 JS 执行**。现所有注入点统一走 `JsLiteral.string`。

两条必须遵守的约定：

1. **新增注入点时必须用 `JsLiteral.string`**，不得自行拼接或手写转义
   （v1.0.0 曾同时存在三套互不一致的转义，其中一套把 `ref` 按 `"` 转义却嵌在
   单引号选择器里）。
2. **ref 定位不经 CSS 解析**：`[data-apex-hash=<ref>]` 会让 CSS 解析器二次解析
   ref，含引号 / 空格 / `]` 的 ref 会抛 `SyntaxError`，并一路冒泡成
   `ElementNotFoundException` + 打开熔断器。故统一走
   「`querySelectorAll('[data-apex-hash]')` + JS `===` 比较属性值」。

## 纯逻辑下沉：页面类型分类（`PageClassifier`）

`pageType` 的信号采集（`BrowserScript.PAGE_TYPE_JS`）与分类逻辑（`PageClassifier`）
已分离。分类策略此前是 `BrowserEngine` 里的 `private` 方法、与 WebView 耦合、
**零测试覆盖**；现下沉到零 Android 依赖的 `:browser-core`，优先级表
（auth > video > form > article > search > list > portal > generic）
由 `PageClassifierTest` 穷举锁定，并对外开放给任何消费方。

> **v1.0.1 移除** `BrowserScript.waitForSelectorJs`：它返回 `Promise`，而
> `evaluateJavascript` 不等待 Promise，回调恒为 `null`，导致
> `browser_navigate(wait_for=…)` 的 `selectorFound` **永远为 false**。
> 已由同步的 `BrowserScript.selectorPresentJs` + Kotlin 侧轮询取代。

## 快照契约（v1.0.1）

| 约定 | 说明 |
|---|---|
| **ref 绝不编造** | `ref` 是 click / input / select 唯一的定位主键，全部经 `data-apex-hash` 反查。缺失时 `ref` 留空并在摘要里标注「不可操作」，而不是回退成 `r1`/`r2` 这类永远解析不到的伪 ref（旧行为会白耗 3 次重试并**打开熔断器**）。 |
| **截断如实上报** | 注入脚本按 `SNAPSHOT_MAX_ELEMENTS` 硬上限截断，现回传 `{v,total,truncated,elements}` 信封；摘要会写明「页面实际匹配 N 个，已达上限」。不告知的话模型会把这一批当成全部。`PageSnapshot.truncated` / `totalCandidateCount` 同步可供程序判断。`DomParser.parse` 仍兼容旧的裸数组形态。 |
| **摘要优先级** | 超 token 预算时按「可交互 > 浅层 > 有标签」决定谁进预算（依赖脚本计算的真实 `depth`，上限 20），但**输出顺序**仍按 `bid` 保持页面自然阅读顺序。 |
| **坐标系统一** | 两条快照路径（`snapshotJs` 与 A11y 降级源）均返回**文档**坐标（加 scroll 偏移），避免物理触摸兜底在 A11y 路径上指向错误位置。 |

## 兼容矩阵（F3：Compose Compiler 版本必须 ≤ 宿主）

| 依赖 | 版本 |
|---|---|
| Kotlin | 2.2.21 |
| AGP | 8.13.2 |
| Gradle | 8.14 |
| Compose BOM | 2025.11.01 |
| Kotlin Coroutines | 1.10.2 |
| kotlinx.serialization | 1.9.0 |
| compileSdk / minSdk | 35 / 26 |

> Requires **Kotlin 2.2.21 + Compose compiler plugin of the same version**（composite build 侧 AGP 必须同为 8.13.2）。

## 消费方式（按阶段递进）

### E1 · 迁移期：Composite Build

宿主 `settings.gradle.kts`：

```kotlin
// 兄弟目录存在时接入（本地开发 / CI 双仓检出）；不存在则走远端坐标
if (file("../apex-browser-kit").isDirectory) {
    includeBuild("../apex-browser-kit")
}
```

宿主 `build.gradle.kts` 依赖照常写远端坐标，Gradle 自动用本地 composite build 替换：

```kotlin
dependencies {
    implementation("com.apex.browser:browser-core:1.2.0")
    implementation("com.apex.browser:browser-engine:1.2.0")
    implementation("com.apex.browser:browser-chrome:1.2.0")
}
```

### E2 · 过渡期：publishToMavenLocal

```bash
./gradlew publishToMavenLocal   # 库侧
# 宿主 repositories 含 mavenLocal() 即可按版本消费
```

### E3 · 稳定期：GitHub Packages

```kotlin
repositories {
    maven {
        url = uri("https://maven.pkg.github.com/AceGuru-mjh/apex-browser-kit")
        credentials {
            username = System.getenv("GITHUB_ACTOR")
            password = System.getenv("GITHUB_TOKEN")
        }
    }
}
```

## DI 接法（本库零 DI 依赖，F1）

库内**不用 Hilt**（避免强绑下游宿主），单例由工厂 object 提供，宿主在自己的
Module 里 provide：

```kotlin
@Module
@InstallIn(SingletonComponent::class)
object BrowserKitModule {
    @Provides @Singleton
    fun provideBrowserEngine(@ApplicationContext ctx: Context): BrowserEngine =
        BrowserEngineFactory.get(ctx)

    @Provides @Singleton
    fun provideApexChromeWiring(@ApplicationContext ctx: Context, engine: BrowserEngine): ApexChromeWiring =
        ApexChromeWiringFactory.get(ctx, engine)

    @Provides @Singleton
    fun provideBrowserOverlay(
        @ApplicationContext ctx: Context,
        engine: BrowserEngine,
        wiring: ApexChromeWiring,
    ): BrowserOverlay = BrowserOverlayFactory.get(ctx, engine, wiring)
}
```

宿主自己的视觉层（悬浮球等）经 [BrowserVisualHook] 注入，库不感知其存在：

```kotlin
class HostVisualHook(private val neonBall: CyberNeonBallManager) : BrowserVisualHook {
    override fun onAgentDrivingStarted() = neonBall.show()
    override fun onWaitingHuman() = neonBall.pulse()
    override fun onSessionHidden() = neonBall.hide()
}
```

## CI 防线（充分利用 GitHub Actions）

### `ci.yml` — 编译与下游兼容（需要 Android SDK）

1. **build-test**：三模块编译 + 单测（`:browser-core:test` 独立跑，证明纯 JVM）+
   `publishToMavenLocal` 可发布性冒烟（坐标/POM 完整性）。
2. **consumer-check**：每次 push/PR 把宿主 [Android-Guru-Agent] 检出为兄弟目录，
   经 composite build 替换后跑宿主 `:app:compileDebugKotlin` + 单测 ——
   **库一改动坏下游，立刻在本仓库 CI 看到**（F6 跨仓库漂移的解法）。

### `guard-rails.yml` — 结构门禁（**秒级，无需 Android SDK**）

编译能过 ≠ 该合。以下都是「便宜地写下来、贵得重新发现」的不变量：

| Job | 拦什么 |
|---|---|
| Library Invariants | `:browser-core` 混进 `android.*` 依赖；依赖箭头反向（core → engine）；注入点绕过 `JsLiteral.string`；零转义 helper 复活；注入脚本返回 `Promise`；**WebView 沙箱被放松**（见下） |
| Resource Integrity | 资源漏 `browser_` 前缀（AGP 只告警，撞的是**宿主** `R`）；`values` 与 `values-*` 键集/占位符不对齐 |
| Structural Quality | God-file 体积预算；`printStackTrace()`；反射派发；括号失衡（词法感知） |
| Supply Chain | 仓库里出现 credential 字面量（PAT/AK/PEM/`password=` 实值）；Gradle 分发包缺 `distributionSha256Sum`；wrapper JAR 被换成 stub；workflow 里 `curl \| sh`；三模块版本号分裂 / README 消费示例过期 / 兼容矩阵漂移；`api/` 基线外出现 public 删除或签名变更（纯新增放行） |
| **Gate Self-Tests** | 每个门禁都拿**真实注入的违规**去验自己会失败 —— 只跑通过的门禁证明不了任何事 |
| Inventory | 文件数 / 行数 / 模块清单 |

### 为什么 WebView 加固要自己写门禁

CodeQL 里所有 WebView 相关查询（`websettings-file-access`、
`webview-addjavascriptinterface`、`improper-webview-certificate-validation` …）
都属于 **`security-extended` 套件，不在默认套件**；开启该套件是**仓库设置**（PR 改不了），
且 Kotlin 分析需要完整构建。在此之前 `check_webview_hardening.py` 是唯一在检查这些
不变量的人 —— 它把 SSL 处理、file/content 访问、混合内容、远程调试、`addJavascriptInterface`
全部锁成门禁。详见 [SECURITY.md](SECURITY.md)。

**一条容易踩的坑**：`allowFileAccessFromFileURLs` 自 API 30 起被系统忽略，但本库
`minSdk 26`，在 API 26~29 上它的默认值仍是 `true`。因此那行 `= false` 是**真起作用的
安全控制，不能当废弃代码删掉** —— 门禁会拦住删除行为。

`dependency-submission.yml` 另把 Gradle 解析结果喂给 GitHub 依赖图，
使 Dependabot 能对**传递依赖**告警（消费方通过我们的坐标间接依赖它们）。

### 宿主自动同步链路（v1.1.0 起）

本仓库 main 的每次合入都会**自动流入宿主** [Android-Guru-Agent]，全程无人工：

```
本仓库 push main
  → notify-host.yml 等待本提交 CI + Guard Rails 全绿（口径见下）
  → repository_dispatch 宿主（event: browser-kit-updated，HOST_SYNC_TOKEN）
  → 宿主 sync-browser-kit.yml 校验上游门禁全绿后 bump gradle/browser-kit.lock
    （PAT 推送 —— GITHUB_TOKEN 推送不触发 workflow，防递归设计）
  → push main 自动点燃宿主 release 流水线 → 新 APK（含本仓库最新代码）发布
```

门禁口径（双侧一致）：只统计**构建健康**类 check-run（CI 的 Build & Test /
Consumer Check + Guard Rails 全套）；显式排除 Dependabot*（长跑依赖升级）、
Gradle dependency graph（数据洞察链路）、notify-host 自身（自引用）。上游有
失败项时宿主**拒绝同步并显性报错**（fail loud），绝不跟红。另有宿主侧 30 分钟
cron 轮询兜底（防丢事件），`gradle/browser-kit.lock` 保证宿主构建可复现。

### 本地复现

```bash
./test.sh          # 门禁 + 门禁自测 + Gradle 单测
./test.sh gates    # 只跑门禁：纯 stdlib Python，不需要 JDK / Android SDK
```

门禁一律写成**可注入违规的自测**，见 `scripts/tests/`。改门禁后请连自测一起跑 ——
正则腐化会让门禁静默变成永远绿的空壳，这是 CI 门禁最常见的失效方式。

## 留在宿主的部分

- `BrowserAgentTools`（browser_* 工具协议：依赖宿主 `AgentTool`/`StreamingAgentTool`）
- `CyberNeonBallManager` / `NeonRingView`（宿主视觉装饰）
- 宿主 DI 接线模块（Hilt）
- `plugin-web-automation` 插件 APK —— 宿主已将其移除，浏览器自动化改以内置
  `:platform:browser-agent` 模块形态集成（见宿主仓库 PR）

## v1.1.0 变更（Agent 可靠性与高级功能）

### 错误率削减（修复既有缺陷）

1. **点击坐标密度换算修复**：旧实现把 `getBoundingClientRect` 的 CSS 像素直接当
   视图物理像素派发——density>1 设备上点击点系统性偏向左上（误点主因）。现按
   「视图宽 / CSS 视口宽」实测缩放系数换算（同时正确处理页面缩放）；
2. **JS 字面量统一转义**：`toJsonString()` 此前零转义——selector 含 CSS 属性
   选择器引号（`[href='x']`）、文本含撇号/换行都会撕裂 JS（静默失败）。现统一
   转义反斜杠/引号/换行/行分隔符 + `</` 序列；
3. **点击前置 scrollIntoView + 遮挡检测**：折叠线以下元素不再坐标越界；
   `elementFromPoint` 命中测试发现粘性顶栏/模态遮罩拦截时下移重试，仍被遮则
   明确报「被遮挡」而非静默误点；
4. **React/Vue 安全输入**：`el.value = x` 直赋改为原型链 native setter +
   input/change 事件（受控组件不再弹回）；contenteditable 支持；多行文本不再
   撕裂 JS；新增 append / pressEnter 模式；
5. **模糊自愈定位**：ref 失配时用快照缓存的 tag+文本模糊重定位并重打原 ref
   ——SPA 局部刷新不再直接失败；新增 `locateElements()` 供 Agent 主动按文本锚定；
6. **动作后真实 diff**：`PostActionState.urlChanged` 旧实现恒 false、
   `newElementsCount` 是当前计数而非增量——现按动作前后探针计算真实 URL 变化
   与元素增量，并携带 `currentUrl`。

### 新增高级 API

| API | 说明 |
|---|---|
| `pressKey(key)` | 键盘事件注入（Enter/Tab/Escape/方向键…），Enter 携带表单隐式提交语义 |
| `hover(ref)` | mouseover/mouseenter/mousemove 悬停序列（下拉菜单、:hover 样式） |
| `drag(fromRef, toRef)` | 触摸拖拽（DOWN → 插值 MOVE → UP），滑块/排序/拖放 |
| `extractContent(mode)` | 结构化抽取：article 正文 / tables 表格 / links 链接 / meta 元信息 |
| `executeJavaScript(js)` | 原生 JS 逃生舱（带超时），长尾页面逻辑兜底 |
| `getCookies(url?)` | 读取 Cookie（登录态诊断） |
| `networkLog(limit, urlContains)` | 网络日志按 URL 子串过滤 |
| `locateElements(text, tag)` | 按文本模糊查找元素并返回可用 ref |

版本：三模块 1.0.0 → **1.1.0**（additive API + 默认参数扩展，宿主源码兼容）。

## v1.2.0 变更（逻辑层文案注入与发布工程）

### ChromeStrings 注入（已知取舍 #1 消解）

chrome 层有三处用户可见文案产生于**逻辑层**而非 Compose 上下文（`stringResource`
不可达）：`BrowserChromeController` 的 6 条 snackbar、`SuggestionsBuilder` 的
4 条联想副标题、`ApexChromeWiring` 的 3 条 JS 弹窗按钮审计标签 —— v1.0.0 起
硬编码中文。v1.2.0 把这 13 条收敛为 `ChromeStrings` 注入接口：

- `ZhChromeStrings`（默认，`ChromeStrings.DEFAULT`）：与旧硬编码**逐字一致**，
  不注入即行为零变化；
- `EnglishChromeStrings`：词汇与 `values-en` 镜像同一套语感
  （Paste and go / URL / Search / Confirm / Deny / Close …）；
- 任意自译实现（其他语言 / 品牌语气）。

接入是**带默认值的尾参**，现有调用点零改动（源码兼容）：

```kotlin
// 控制器直构（BrowserChrome 的 controller 参数同款注入点）
val controller = BrowserChromeController(gateway, scripts, config, EnglishChromeStrings)

// 接线器工厂（宿主 DI Module 里同款）
val wiring = ApexChromeWiringFactory.get(ctx, engine, EnglishChromeStrings)
```

> `ApexChromeWiringFactory` 是进程级单例：**首次**调用的 `strings` 生效，切换
> 实现请保证首次就传入（或测试里 `reset()`）。`SuggestionsBuilder.build` 的
> `strings` 尾参由控制器自动透传，纯函数调用方也可独立注入。

Compose UI 层文案继续走 `res/values` + `values-en`（`browser_` 前缀强制），
两套机制各管一层：UI 层跟系统语言，逻辑层跟注入实现。契约由
`ChromeStringsTest` 锁定（中文逐字一致 / 两实现 13 条非空 / `tabSwitchHint`
含 host / 接口成员计数防漂移）。

### 发布工程

| 项 | 说明 |
|---|---|
| `tag-release.yml` | push main 自动读三模块 `version =`（分裂即 fail，与 `check_versions.py` RULE 1 同口径），无对应 tag 则 `git tag vX.Y.Z` 并推送（幂等：已存在跳过不报错；版本回退只跳过不强推）。tag push 不会点燃 `notify-host.yml`（其 `on:` 仅 `push.branches=[main]`，且 GITHUB_TOKEN push 本就不触发 workflow，双保险） |
| 版本可追溯 | 此前版本只存在于 `build.gradle.kts`、从未留下 git tag；现在每个发布列车落成 `vTag`，坐标 ↔ 源码状态一一对应 |

版本：三模块 1.1.0 → **1.2.0**（additive API，全部为带默认值参数的尾参扩展，
宿主源码兼容）。

## 已知取舍（v1.0.0）

- 逻辑层文案硬编码中文（`BrowserChromeController` 5 条 snackbar 与
  `SuggestionsBuilder` 联想副标题，非 Compose 上下文、`stringResource` 不可达）
  —— **v1.2.0 已消解**：连同 `ApexChromeWiring` 的 3 条 JS 弹窗审计标签共
  13 条收敛为 `ChromeStrings` 注入接口，默认 `ZhChromeStrings` 与旧硬编码
  逐字一致，可换 `EnglishChromeStrings` 或自译实现（见「v1.2.0 变更」）。
  chrome UI 层文案始终资源化（`res/values` + `values-en`，`browser_` 前缀强制）。
- 库 manifest 不声明 `SYSTEM_ALERT_WINDOW` —— 由宿主自行声明（并非所有消费者都
  需要浮窗，F5）。

## 来源追溯

- 抽取自 `Android-Guru-Agent@139ffee`（main, 2026-10）：
  - `core/tool-registry/.../builtin/browser/` → `:browser-core`
  - `app/.../browser/{BrowserEngine,BrowserTracer,OverlayLifecycleOwner,RetryPolicy}.kt` → `:browser-core` / `:browser-engine`
  - `app/.../browser/chrome/**` + `BrowserOverlay.kt` → `:browser-chrome`
- 两仓库 commit 互相引用宿主侧删除提交与库侧首个提交。

## License

MIT
