package com.apex.browser.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * [JsLiteral] 的「意图」：**任意**输入字符串都必须编码成语法合法的 JS 单引号字面量，
 * 且解码后与原串**完全一致**。
 *
 * 这是一条安全不变量，不是实现细节 —— [JsLiteral.string] 是本库把 Agent / 页面
 * 内容送进 `evaluateJavascript` 的唯一编码入口。一旦漏转义，浏览器 Agent 的间接提示
 * 注入就获得了页面内任意 JS 执行能力（网页正文 → 快照 → Agent 回传的 ref →
 * 注入脚本）。
 *
 * 测试以「解码回原文」为主断言（比逐条子串断言更强且无歧义），辅以结构与转义面检查。
 */
class JsLiteralTest {

    /**
     * 解析一个**单引号包裹**的 JS 字符串字面量，返回解码后的内容。
     * 仅用于测试：它是 [JsLiteral.string] 的逆运算，若实现与本解析器对不上，
     * 说明生成的 JS 语义会漂移 —— 那正是最危险的一类 bug。
     */
    private fun decodeSingleQuoted(literal: String): String {
        require(literal.startsWith("'") && literal.endsWith("'")) { "not a quoted literal: $literal" }
        val body = literal.substring(1, literal.length - 1)
        val sb = StringBuilder()
        var i = 0
        while (i < body.length) {
            val c = body[i]
            if (c != '\\') {
                require(c != '\'') { "unescaped quote closes literal early: $literal" }
                sb.append(c)
                i++
                continue
            }
            require(i + 1 < body.length) { "dangling backslash: $literal" }
            when (val esc = body[i + 1]) {
                '\\' -> sb.append('\\')
                '\'' -> sb.append('\'')
                'n' -> sb.append('\n')
                'r' -> sb.append('\r')
                't' -> sb.append('\t')
                'b' -> sb.append('\b')
                'f' -> sb.append('\u000C')
                'u' -> {
                    val hex = body.substring(i + 2, i + 6)
                    require(hex.length == 4 && hex.all { it in "0123456789abcdef" }) {
                        "bad \\u escape: $literal"
                    }
                    sb.append(hex.toInt(16).toChar())
                    i += 4
                }
                else -> error("unknown escape '\\$esc' in: $literal")
            }
            i += 2
        }
        return sb.toString()
    }

    /** 核心不变量：编码 → 解码 必须还原原串。 */
    private fun assertRoundTrip(input: String) {
        val literal = JsLiteral.string(input)
        assertEquals(
            "编码后解码必须还原原串",
            input,
            decodeSingleQuoted(literal),
        )
    }

    // ---- 核心往返不变量 ----

    @Test
    fun `往返不变量对代表性输入全部成立`() {
        val corpus = listOf(
            "",                        // 空串
            "abc",
            "r_3k9f",                  // 语义哈希 ref 的真实形态
            "a.login",                 // 常见选择器
            "input[type=search]",
            "https://x.com/a?b=1&c=2",
            "搜索",                     // CJK
            "🚀ok",                    // 代理对 / emoji
            "it's",                    // 单引号
            "'",                       // 纯单引号
            "\"",                      // 双引号
            "\\",                      // 纯反斜杠
            "\\'",                     // 反斜杠 + 单引号（易被二次解释）
            "line1\nline2",            // 换行
            "a\r\nb",                  // CRLF
            "\t\b\u000C",              // 其余空白类控制符
            "\u2028\u2029",            // JS 字符串字面量非法码点
            "\u0000\u001f\u007f",      // C0 与 DEL
            "x']);alert(1);//",        // 单引号闭合注入
            "a\"]);alert(1);//",       // 双引号闭合注入
            "]' OR '1'='1",            // CSS/属性选择器风格载荷
            "混合'\"\\的\n换行\u2028行",
            // 以下取自浏览器自动化领域常用的对抗语料（Playwright / browser-use
            // 系的转义测试清单）：它们未必能在本库形成注入，但都是手写转义器
            // 经典的漏网之处，进语料即可锁住「解码后内容不变」这一不变量。
            "</script>",               // HTML 解析器层面的经典闭合序列
            "</SCRIPT>",
            "`",                        // 模板字符串定界符
            "$",
            "\${}",                    // 字面量 ${}
            "//",
            "/*",
            "*/",
            "a".repeat(4096),          // 超长输入
        )
        for (input in corpus) {
            assertRoundTrip(input)
        }
    }

    @Test
    fun `对抗语料在字面量内不留可闭合序列`() {
        // 意图：整串必须是一个完整字面量 —— 内部既不能出现未转义的单引号，
        // 也不能出现裸换行/裸回车把字面量截断。
        val adversarial = listOf(
            "</script>", "</SCRIPT>", "`", "$", "\${}", "//", "/*", "*/",
            " ", "a".repeat(4096), "'", "\\", "\n",
        )
        for (input in adversarial) {
            val literal = JsLiteral.string(input)
            val body = literal.substring(1, literal.length - 1)
            var i = 0
            while (i < body.length) {
                if (body[i] == '\\') { i += 2; continue }
                assertTrue("残留未转义单引号: ${input.take(12)}", body[i] != '\'')
                i++
            }
            assertTrue("残留裸换行/回车", body.none { it == '\n' || it == '\r' })
        }
    }

    @Test
    fun `穷举全部 C0 控制字符与 DEL 均满足往返`() {
        val all = buildString {
            for (code in 0..0x1F) append(code.toChar())
            append('\u007F')
        }
        assertRoundTrip(all)
    }

    // ---- 结构契约 ----

    @Test
    fun `字面量始终以单引号包裹且闭合`() {
        for (input in listOf("", "abc", "it's", "'", "\\", "\n", "混合'与\\与\n")) {
            val out = JsLiteral.string(input)
            assertTrue("应以单引号开头: $out", out.startsWith("'"))
            assertTrue("应以单引号结尾: $out", out.endsWith("'"))
        }
    }

    @Test
    fun `字面量内不得出现未转义的单引号`() {
        // 逐位置检查：每个单引号都必须紧跟一个转义反斜杠。
        for (input in listOf("it's", "'", "a'b'c", "x']);alert(1);//", "'\\'")) {
            val body = JsLiteral.string(input).substring(1, JsLiteral.string(input).length - 1)
            var i = 0
            while (i < body.length) {
                if (body[i] == '\\') {
                    i += 2 // 跳过被转义的字符
                    continue
                }
                assertTrue("位置 $i 出现未转义单引号: $body", body[i] != '\'')
                i++
            }
        }
    }

    @Test
    fun `字面量内不得出现裸换行或裸回车`() {
        // JS 单行字符串字面量内的裸换行是语法错误：整段 evaluateJavascript 直接失败，
        // 而非「输入失败」—— 这正是往 <textarea> 填多行文本静默失效的根因。
        for (input in listOf("a\nb", "a\rb", "a\r\nb", "\n")) {
            val out = JsLiteral.string(input)
            assertEquals("不得含裸换行/回车: $out", 0, out.count { it == '\n' || it == '\r' })
        }
        assertTrue(JsLiteral.string("a\nb").contains("\\n"))
        assertTrue(JsLiteral.string("a\rb").contains("\\r"))
    }

    @Test
    fun `U+2028 与 U+2029 必须转义`() {
        // 这两个码点在 JSON 中合法、在 ES2019 前的 JS 字符串字面量中非法，
        // 是序列化边界上极隐蔽的一个坑。
        val out = JsLiteral.string("a\u2028b\u2029c")
        assertTrue("U+2028 必须转义: $out", out.contains("\\u2028"))
        assertTrue("U+2029 必须转义: $out", out.contains("\\u2029"))
        assertEquals("不得残留裸码点", 0, out.count { it == '\u2028' || it == '\u2029' })
    }

    @Test
    fun `unicode 转义为小写四位十六进制`() {
        assertEquals("'\\u0000'", JsLiteral.string("\u0000"))
        assertEquals("'\\u001f'", JsLiteral.string("\u001f"))
        assertEquals("'\\u007f'", JsLiteral.string("\u007F"))
    }

    // ---- 过度转义（反向缺陷）防护：普通内容必须原样保留 ----

    @Test
    fun `普通内容与 CJK 不被多余转义`() {
        assertEquals("'r_3k9f'", JsLiteral.string("r_3k9f"))
        assertEquals("'搜索'", JsLiteral.string("搜索"))
        assertEquals("'a.login'", JsLiteral.string("a.login"))
        assertEquals("'https://x.com/a?b=1&c=2'", JsLiteral.string("https://x.com/a?b=1&c=2"))
    }

    @Test
    fun `双引号与中点在单引号字面量中保持原样`() {
        // JsLiteral 刻意产出单引号字面量，故 `"` 只是普通字符。
        // 旧 highlightJs 把 ref 放进**双引号** CSS 选择器，那才是真正的注入面。
        assertEquals("'say \"hi\"'", JsLiteral.string("say \"hi\""))
        assertEquals("'·'", JsLiteral.string("·"))
    }

    @Test
    fun `emoji 与代理对不被破坏`() {
        assertEquals("'🚀ok'", JsLiteral.string("🚀ok"))
    }

    @Test
    fun `空串产出合法空字面量`() {
        assertEquals("''", JsLiteral.string(""))
        assertEquals("", decodeSingleQuoted("''"))
    }
}