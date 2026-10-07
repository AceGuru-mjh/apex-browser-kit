package com.apex.browser.core

/**
 * 把**不可信字符串**编码为合法的 JS 字面量 —— 本库向 WebView 注入 JS 的唯一编码入口。
 *
 * ## 为什么必须专章处理
 *
 * 本库注入的 JS 里大量嵌入外部字符串：[BrowserScript] 的 `ref` / `selector` / `value` /
 * `text`，全部来自 Agent 工具参数。而浏览器 Agent 天然暴露在**间接提示注入**下：
 * 页面正文可以把「看起来像 ref 的恶意串」写进快照，诱导模型原样回传。若原样拼进
 * `evaluateJavascript`，页面内容即可闭合字符串字面量并执行任意 JS —— 即
 * *网页内容 → Agent 上下文 → 注入脚本* 的闭环提权。
 *
 * 旧实现是 `private fun String.toJsonString() = "'$this'"` —— 函数名与 KDoc 声称
 * "把字符串安全包成 JS 单引号字面量"，实现却**一个字符都不转义**。修复前
 * `BrowserScript.rectByRefJs("a']);alert(1);//")` 生成的 JS 会被页面执行。
 *
 * ## 覆盖的转义面
 *
 * - `\` 与 `'` —— 闭合字面量的两个必需字符；
 * - `\n` `\r` `\t` `\b` `\f` —— 控制字符。**换行必须转义**：JS 单行字符串字面量内的
 *   裸换行是语法错误，整段 `evaluateJavascript` 直接失败而非「输入失败」——
 *   往 `<textarea>` 填多行文本会静默失效（这是本库的既有真实故障）。
 * - U+2028 / U+2029 —— 在 JSON 中合法、在 JS 字符串字面量中非法（ES2019 前），
 *   是著名的跨序列化陷阱；
 * - 其余 C0 控制字符与 DEL —— 一律 `\uXXXX`，杜绝不可见字符注入。
 *
 * 采用单引号字面量（而非 JSON 的双引号 + 引号包裹）是刻意选择：本库所有注入点都以
 * 单引号拼接上下文（`'[data-apex-hash=' + ref + ']'`），保持一致可读。
 */
object JsLiteral {

    private const val HEX = "0123456789abcdef"

    /**
     * 把 [value] 编码为**单引号包裹**的 JS 字符串字面量（含首尾引号）。
     *
     * 纯 Kotlin、无 Android 依赖，可在 JVM 单测中穷举验证 —— 见 `JsLiteralTest`。
     * 本函数是幂等边界清晰的纯函数：任意 [value]（含空串、含换行、含引号、含控制字符、
     * 含 emoji/代理对）都返回语法合法的 JS 字符串字面量。
     */
    fun string(value: String): String {
        val sb = StringBuilder(value.length + 2)
        sb.append('\'')
        for (ch in value) {
            when (ch) {
                '\\' -> sb.append("\\\\")
                '\'' -> sb.append("\\'")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                '\t' -> sb.append("\\t")
                '\b' -> sb.append("\\b")
                '\u000C' -> sb.append("\\f")
                // U+2028 LINE SEPARATOR / U+2029 PARAGRAPH SEPARATOR：
                // JSON 合法但 JS 字符串字面量非法，必须转义
                '\u2028', '\u2029' -> sb.append(unicodeEscape(ch.code))
                else ->
                    if (ch < ' ' || ch == '\u007F') sb.append(unicodeEscape(ch.code))
                    else sb.append(ch)
            }
        }
        sb.append('\'')
        return sb.toString()
    }

    /**
     * `\uXXXX` 转义。手工拼装而非 `String.format`，以免受默认 Locale 影响
     * （部分 Locale 下 `%x` 可能产生非 ASCII 数字，直接破坏生成的 JS）。
     */
    private fun unicodeEscape(code: Int): String {
        val sb = StringBuilder(6)
        sb.append("\\u")
        for (shift in intArrayOf(12, 8, 4, 0)) {
            sb.append(HEX[(code shr shift) and 0xF])
        }
        return sb.toString()
    }
}