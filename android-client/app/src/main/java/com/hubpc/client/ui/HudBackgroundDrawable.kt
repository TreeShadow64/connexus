package com.hubpc.client.ui

import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ColorFilter
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.RadialGradient
import android.graphics.Shader
import android.graphics.drawable.Drawable

/** Sfondo della dashboard PC: sfumatura scura con luce in alto al centro,
 * griglia da 42px che sfuma radialmente e righe di scansione sottili
 * (`body` + `.hud-grid-bg` + `.hud-scanline` in styles.css). Usato come
 * `windowBackground` del tema, quindi ogni schermata lo eredita. */
class HudBackgroundDrawable : Drawable() {

    private val basePaint = Paint()
    private val glowPaint = Paint()
    private val gridPaint = Paint().apply {
        style = Paint.Style.STROKE
        strokeWidth = 1f
    }
    private val scanPaint = Paint().apply {
        style = Paint.Style.STROKE
        strokeWidth = 1f
        color = Color.argb(7, 255, 255, 255)
    }

    private var density = 2.75f

    override fun inflate(
        r: android.content.res.Resources,
        parser: org.xmlpull.v1.XmlPullParser,
        attrs: android.util.AttributeSet,
        theme: android.content.res.Resources.Theme?,
    ) {
        super.inflate(r, parser, attrs, theme)
        density = r.displayMetrics.density
    }

    override fun onBoundsChange(bounds: android.graphics.Rect) {
        val w = bounds.width().toFloat()
        val h = bounds.height().toFloat()
        if (w <= 0f || h <= 0f) return

        basePaint.shader = LinearGradient(
            0f, 0f, 0f, h,
            intArrayOf(0xFF0A1826.toInt(), 0xFF060D16.toInt(), 0xFF030608.toInt()),
            floatArrayOf(0f, 0.45f, 1f),
            Shader.TileMode.CLAMP,
        )
        // alone di luce in alto al centro (l'"ellipse at 50% 0%" del CSS)
        glowPaint.shader = RadialGradient(
            w / 2f, 0f, w * 0.95f,
            intArrayOf(0x660A1826, 0x00060D16),
            floatArrayOf(0f, 1f),
            Shader.TileMode.CLAMP,
        )
        // griglia visibile solo vicino al centro-alto (mask radiale del CSS)
        gridPaint.shader = RadialGradient(
            w / 2f, h * 0.2f, maxOf(w, h * 0.6f) * 0.75f,
            intArrayOf(0x246FE3FF, 0x00000000),
            floatArrayOf(0f, 1f),
            Shader.TileMode.CLAMP,
        )
    }

    override fun draw(canvas: Canvas) {
        val b = bounds
        if (b.isEmpty) return
        canvas.drawRect(b, basePaint)
        canvas.drawRect(b, glowPaint)

        val step = 42f * density
        var x = b.left.toFloat()
        while (x <= b.right) {
            canvas.drawLine(x, b.top.toFloat(), x, b.bottom.toFloat(), gridPaint)
            x += step
        }
        var y = b.top.toFloat()
        while (y <= b.bottom) {
            canvas.drawLine(b.left.toFloat(), y, b.right.toFloat(), y, gridPaint)
            y += step
        }

        val scanStep = 3f * density
        var sy = b.top.toFloat()
        while (sy <= b.bottom) {
            canvas.drawLine(b.left.toFloat(), sy, b.right.toFloat(), sy, scanPaint)
            sy += scanStep
        }
    }

    override fun setAlpha(alpha: Int) {}
    override fun setColorFilter(colorFilter: ColorFilter?) {}

    @Deprecated("Deprecated in Java")
    override fun getOpacity(): Int = PixelFormat.OPAQUE
}
