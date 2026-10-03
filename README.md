# apex-browser-kit

Embedded WebView automation engine with Compose chrome UI — 从
[Android-Guru-Agent](https://github.com/AceGuru-mjh/Android-Guru-Agent) 拆出的浏览器能力库。

[![CI](https://github.com/AceGuru-mjh/apex-browser-kit/actions/workflows/ci.yml/badge.svg)](https://github.com/AceGuru-mjh/apex-browser-kit/actions/workflows/ci.yml)
![Consumer](https://img.shields.io/badge/consumer-Android--Guru--Agent-blue)

## 模块结构

| 模块 | 类型 | 职责 |
|---|---|---|
| `:browser-core` | 纯 Kotlin JVM（零 Android 依赖） | DOM 模型（`DomElement`/`PageSnapshot`）、注入 JS（`BrowserScript`）、快照解析（`DomParser`）、重试/熔断（`RetryPolicy`/`CircuitBreaker`） |
| `:browser-engine` | Android Library（无 UI） | 无头 WebView 自动化引擎（`BrowserEngine`）：状态机、标签管理、物理触摸注入、加载等待、Cookie 持久化、崩溃恢复 |
| `:browser-chrome` | Android Library（Compose UI） | 「给人用的浏览器界面」：地址胶囊即进度条、标签条/总览、页内查找、下载面板、JS 弹窗/权限浮层、浮窗宿主（`BrowserOverlay`） |

依赖方向：`chrome → engine → core`（`api` 传递，下游引 chrome 即得全套类型）。

## 兼容矩阵（F3：Compose Compiler 版本必须 ≤ 宿主）

| 依赖 | 版本 |
|---|---|
| Kotlin | 2.0.21 |
| AGP | 8.7.3 |
| Compose BOM | 2024.12.01 |
| Kotlin Coroutines | 1.9.0 |
| kotlinx.serialization | 1.7.3 |
| compileSdk / minSdk | 35 / 26 |

> Requires **Kotlin 2.0.21 + Compose compiler plugin of the same version**。

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
    implementation("com.apex.browser:browser-core:1.0.0")
    implementation("com.apex.browser:browser-engine:1.0.0")
    implementation("com.apex.browser:browser-chrome:1.0.0")
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

本仓库 `.github/workflows/ci.yml` 有两道防线：

1. **build-test**：三模块编译 + 单测（`:browser-core:test` 独立跑，证明纯 JVM）+
   `publishToMavenLocal` 可发布性冒烟（坐标/POM 完整性）。
2. **consumer-check**：每次 push/PR 把宿主 [Android-Guru-Agent] 检出为兄弟目录，
   经 composite build 替换后跑宿主 `:app:compileDebugKotlin` + 单测 ——
   **库一改动坏下游，立刻在本仓库 CI 看到**（F6 跨仓库漂移的解法）。

## 留在宿主的部分

- `BrowserAgentTools`（browser_* 工具协议：依赖宿主 `AgentTool`/`StreamingAgentTool`）
- `CyberNeonBallManager` / `NeonRingView`（宿主视觉装饰）
- 宿主 DI 接线模块（Hilt）
- `plugin-web-automation` 插件 APK（宿主插件体系分发壳，与本库无版本耦合）

## 已知取舍（v1.0.0）

- `BrowserChromeController` 的 5 条 snackbar 文案与 `SuggestionsBuilder` 的联想
  副标题为**逻辑层字符串**（非 Compose 上下文，`stringResource` 不可达），暂保持
  硬编码中文；chrome UI 层全部文案已资源化（`res/values` + `values-en`，
  `browser_` 前缀强制）。
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
