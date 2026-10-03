package com.apex.browser.chrome

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.LocationOn
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.ui.res.stringResource

/*
 * PermissionRequestHost —— 网页权限请求（相机/麦克风/定位/通知…）。
 *
 * 优于独立 Activity 方案：
 * - 在浮窗内原地弹 Compose 对话框，无跨 Activity 跳转、无冷启动；
 * - 三档决策：拒绝 / 仅此一次 / 总是允许（「总是」的持久化由引擎侧按 origin 落库）。
 */

@Composable
private fun resourceMeta(res: WebPermissionResource): Triple<ImageVector, String, String> = when (res) {
    WebPermissionResource.CAMERA -> Triple(Icons.Filled.CameraAlt, stringResource(R.string.browser_perm_camera), stringResource(R.string.browser_perm_camera_desc))
    WebPermissionResource.MICROPHONE -> Triple(Icons.Filled.Mic, stringResource(R.string.browser_perm_microphone), stringResource(R.string.browser_perm_microphone_desc))
    WebPermissionResource.LOCATION -> Triple(Icons.Filled.LocationOn, stringResource(R.string.browser_perm_location), stringResource(R.string.browser_perm_location_desc))
    WebPermissionResource.NOTIFICATIONS -> Triple(Icons.Filled.Notifications, stringResource(R.string.browser_perm_notifications), stringResource(R.string.browser_perm_notifications_desc))
    WebPermissionResource.PROTECTED_MEDIA_ID -> Triple(Icons.Filled.Warning, stringResource(R.string.browser_perm_protected_media), stringResource(R.string.browser_perm_protected_media_desc))
    WebPermissionResource.MIDI -> Triple(Icons.Filled.Warning, stringResource(R.string.browser_perm_midi), stringResource(R.string.browser_perm_midi_desc))
}

@Composable
fun PermissionRequestHost(gateway: BrowserEngineGateway) {
    val requests by gateway.permissionRequests.collectAsStateWithLifecycle()
    val request = requests.firstOrNull() ?: return
    val p = chromePalette()

    // 浮窗安全：ChromeScrimDialog 自绘（AlertDialog 在 overlay 缺窗口 token 会崩）
    ChromeScrimDialog(
        onDismiss = { gateway.resolvePermission(request.id, PermissionDecision.DENY) },
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(
                resourceMeta(request.resources.first()).first,
                contentDescription = null,
                tint = p.warn,
                modifier = Modifier.size(22.dp),
            )
            Spacer(Modifier.padding(start = 8.dp))
            Text(
                text = stringResource(R.string.browser_perm_request_title, request.originHost),
                fontSize = 15.sp,
                fontWeight = FontWeight.Medium,
            )
        }
        Spacer(Modifier.padding(top = 10.dp))
        Text(stringResource(R.string.browser_perm_request_body), fontSize = 13.sp)
        Spacer(Modifier.padding(top = 8.dp))
        request.resources.forEach { res ->
            val (_, label, detail) = resourceMeta(res)
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(
                    resourceMeta(res).first,
                    contentDescription = label,
                    tint = p.accent,
                    modifier = Modifier.size(18.dp),
                )
                Spacer(Modifier.padding(start = 10.dp))
                Column {
                    Text(label, fontSize = 13.sp, fontWeight = FontWeight.Medium)
                    Text(detail, fontSize = 11.sp, color = p.textSecondary)
                }
            }
        }
        Text(
            stringResource(R.string.browser_perm_always_note),
            fontSize = 10.sp,
            color = p.textSecondary,
            modifier = Modifier.padding(top = 8.dp),
        )
        Spacer(Modifier.padding(top = 6.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            TextButton(onClick = {
                gateway.resolvePermission(request.id, PermissionDecision.DENY)
            }) { Text(stringResource(R.string.browser_perm_deny)) }
            Spacer(Modifier.weight(1f))
            FilledTonalButton(onClick = {
                gateway.resolvePermission(request.id, PermissionDecision.GRANT_ONCE)
            }) { Text(stringResource(R.string.browser_perm_grant_once)) }
            Spacer(Modifier.padding(start = 6.dp))
            FilledTonalButton(
                onClick = { gateway.resolvePermission(request.id, PermissionDecision.GRANT_ALWAYS) },
            ) {
                Icon(Icons.Filled.Check, contentDescription = null, modifier = Modifier.size(14.dp))
                Spacer(Modifier.padding(start = 4.dp))
                Text(stringResource(R.string.browser_perm_grant_always))
            }
        }
    }
}
