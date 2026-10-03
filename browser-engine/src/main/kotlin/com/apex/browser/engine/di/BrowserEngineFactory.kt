package com.apex.browser.engine.di

import android.content.Context
import com.apex.browser.engine.BrowserEngine

/**
 * 引擎进程级单例工厂（库化去 Hilt 的替代方案，见 plan 阶段 B2 改造 1）。
 *
 * 本库**不依赖任何 DI 框架**（F1：Hilt + Android Library 组合会强绑下游宿主），
 * 单例语义由本 object 提供。宿主的两种接法：
 *
 * 1. 手动：`val engine = BrowserEngineFactory.get(context)`
 * 2. Hilt（宿主自己的 Module，库无感知）：
 * ```
 * @Module
 * @InstallIn(SingletonComponent::class)
 * object BrowserModule {
 *     @Provides @Singleton
 *     fun provideBrowserEngine(@ApplicationContext ctx: Context): BrowserEngine =
 *         BrowserEngineFactory.get(ctx)
 * }
 * ```
 */
object BrowserEngineFactory {

    @Volatile
    private var instance: BrowserEngine? = null

    fun get(context: Context): BrowserEngine =
        instance ?: synchronized(this) {
            instance ?: BrowserEngine.create(context).also { instance = it }
        }

    /** 仅测试用：重置单例。 */
    @Synchronized
    fun reset() {
        instance = null
    }
}
