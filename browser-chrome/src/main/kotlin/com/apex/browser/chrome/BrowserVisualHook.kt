package com.apex.browser.chrome

/**
 * 宿主视觉层挂钩（库化改造 B3 改造 2：BrowserOverlay 与宿主视觉装饰解耦）。
 *
 * 库不知道宿主的悬浮球 / 通知 / 震动等视觉元素的存在 —— 宿主实现本接口注入
 * [BrowserOverlay]，在会话状态变化时驱动自己的视觉层（霓虹球脉冲、通知等）。
 *
 * 宿主示例：
 * ```
 * class HostVisualHook(private val neonBall: CyberNeonBallManager) : BrowserVisualHook {
 *     override fun onAgentDrivingStarted() = neonBall.show()
 *     override fun onWaitingHuman() = neonBall.pulse()
 *     override fun onSessionHidden() = neonBall.hide()
 * }
 * ```
 */
interface BrowserVisualHook {
    /** 会话进入 Agent 驾驶态（引擎被真实使用，可视层按需出现）。 */
    fun onAgentDrivingStarted() {}

    /** 人工接管中（浮窗已展开，可视层切换 NEED_HUMAN 观感：脉冲 / 抖动 / badge）。 */
    fun onWaitingHuman() {}

    /** 会话隐藏（本次浏览结束，可视层收起）。 */
    fun onSessionHidden() {}
}
