# browser-engine consumer rules（宿主 release 构建自动合并）
# 引擎公开 API 与状态机（Hilt 反射/序列化兜底）
-keep class com.apex.browser.engine.BrowserEngine { *; }
-keep class com.apex.browser.engine.BrowserEngine$* { *; }
-keep class com.apex.browser.engine.BrowserEngineFactory { *; }
-keep class com.apex.browser.engine.BrowserTracer { *; }
-keep class com.apex.browser.engine.BrowserTracer$* { *; }
-keep class com.apex.browser.engine.OverlayLifecycleOwner { *; }
# 错误类型：跨模块 catch 语义依赖类名
-keep class com.apex.browser.engine.HandoffLockedException { *; }
