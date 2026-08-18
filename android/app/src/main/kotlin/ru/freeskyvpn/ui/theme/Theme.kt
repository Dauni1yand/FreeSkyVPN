package ru.freeskyvpn.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * One theme, always dark.
 *
 * The system setting is deliberately not consulted. A light variant would
 * double the surface to design and to keep consistent for an app whose whole
 * screen is one button — and a VPN toggle reads better against black anyway.
 * If a light theme is ever wanted, it goes here, not as scattered `if`s.
 *
 * The palette is Apple's dark system colours rather than Material's. Material
 * dark is a very dark grey with tinted surfaces; iOS dark is true black with
 * neutral greys that step in fixed increments. The second is what "минималистично,
 * как эпловский дизайн" means in practice, so the values below are those steps
 * rather than a Material tonal palette.
 */

// iOS dark system greys. The numbers are the actual system values, kept as
// they are so the steps between surfaces stay even.
private val Black = Color(0xFF000000)
private val Grey6 = Color(0xFF1C1C1E)   // cards on black
private val Grey5 = Color(0xFF2C2C2E)   // raised elements
private val Grey4 = Color(0xFF3A3A3C)   // separators, inactive tracks
private val Grey2 = Color(0xFFAEAEB2)   // secondary label
private val Label = Color(0xFFF2F2F7)   // primary label

// System accents. Green is reserved for "connected" and nothing else — an
// accent that also means something specific stops meaning it.
internal val SystemGreen = Color(0xFF30D158)
internal val SystemBlue = Color(0xFF0A84FF)
internal val SystemRed = Color(0xFFFF453A)
internal val SystemOrange = Color(0xFFFF9F0A)

private val Scheme = darkColorScheme(
    primary = SystemBlue,
    onPrimary = Color.White,
    secondary = Grey2,
    onSecondary = Black,
    background = Black,
    onBackground = Label,
    surface = Grey6,
    onSurface = Label,
    surfaceVariant = Grey5,
    onSurfaceVariant = Grey2,
    outline = Grey4,
    error = SystemRed,
    onError = Color.White,
)

/**
 * San Francisco is not licensable off Apple platforms, so this is the system
 * face with SF's *metrics* — heavy large titles with negative tracking,
 * tight line heights — which is what actually reads as the style.
 */
private val AppTypography = Typography(
    displayLarge = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Bold,
        fontSize = 40.sp,
        lineHeight = 46.sp,
        letterSpacing = (-1.0).sp,
    ),
    headlineLarge = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Bold,
        fontSize = 30.sp,
        lineHeight = 36.sp,
        letterSpacing = (-0.6).sp,
    ),
    titleLarge = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 20.sp,
        lineHeight = 25.sp,
        letterSpacing = (-0.3).sp,
    ),
    titleMedium = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Medium,
        fontSize = 17.sp,
        lineHeight = 22.sp,
        letterSpacing = (-0.2).sp,
    ),
    bodyLarge = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 17.sp,
        lineHeight = 22.sp,
        letterSpacing = (-0.2).sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 15.sp,
        lineHeight = 20.sp,
    ),
    labelLarge = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 17.sp,
        lineHeight = 22.sp,
        letterSpacing = (-0.2).sp,
    ),
    labelMedium = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Medium,
        fontSize = 13.sp,
        lineHeight = 18.sp,
    ),
)

/** Corner radii and insets, in one place so the spacing stays regular. */
object Metrics {
    val cardCorner = 14.dp
    val screenPadding = 20.dp
    val itemSpacing = 12.dp
    val rowHeight = 52.dp
}

@Composable
fun FreeSkyTheme(content: @Composable () -> Unit) =
    MaterialTheme(colorScheme = Scheme, typography = AppTypography, content = content)
