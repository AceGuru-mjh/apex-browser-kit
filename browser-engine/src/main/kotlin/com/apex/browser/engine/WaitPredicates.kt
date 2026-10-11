package com.apex.browser.engine

import android.webkit.WebView
import kotlin.coroutines.resume
import kotlinx.coroutines.suspendCancellableCoroutine

/**
 * `waitForCondition(selector_count)` 的谓词支撑（v1.3.0，internal）。
 *
 * 独立成文件：谓词解析是纯字符串逻辑（可独立演进），且 BrowserEngine.kt 已贴近
 * 非注释行预算（1200，check_code_quality GATE 1）。对消费方不可见（internal）。
 */
internal class CountPredicate(val selector: String, val op: String, val n: Int)

/**
 * 解析 selector_count 的 value（尾部解析）：算子形态 `<选择器>>=10` 支持任意空白；
 * 纯数字形态（语义为 `>=`，「等到至少 N 个」是等待场景的主导用法）要求与选择器间
 * 有空白 —— 避免与 `.item2` 这类以数字结尾的选择器歧义。解析失败返回 null
 * （调用方据此给出可诊断错误而非静默超时）。
 */
internal fun parseCountPredicate(value: String): CountPredicate? {
    val v = value.trim()
    Regex("^(.*?)(>=|<=|==|>|<)\\s*(\\d+)$").find(v)?.let {
        val sel = it.groupValues[1].trim()
        if (sel.isNotEmpty()) return CountPredicate(sel, it.groupValues[2], it.groupValues[3].toInt())
    }
    Regex("^(.+?)\\s+(\\d+)$").find(v)?.let { return CountPredicate(it.groupValues[1].trim(), ">=", it.groupValues[2].toInt()) }
    return null
}

internal fun matchesCount(count: Int, p: CountPredicate): Boolean = when (p.op) {
    ">=" -> count >= p.n
    "<=" -> count <= p.n
    "==" -> count == p.n
    ">" -> count > p.n
    else -> count < p.n
}

/**
 * 数值型求值（v1.3.0，internal）：WebView 回传的 JSON 数字串（如 `"3"`）转 Int；
 * 非数值形状（null / 异常）返回 null。network_idle 与 selector_count 轮询的取值侧。
 */
internal suspend fun WebView.evaluateAsInt(js: String): Int? =
    suspendCancellableCoroutine { cont ->
        evaluateJavascript(js) { result -> cont.resume(result?.trim()?.toIntOrNull()) }
    }
