package com.hubpc.client.ui

import android.content.res.Resources
import android.graphics.Canvas
import android.graphics.ColorFilter
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.RectF
import android.graphics.drawable.Drawable
import android.util.AttributeSet
import com.hubpc.client.R
import org.xmlpull.v1.XmlPullParser

/** Forma HUD della dashboard PC: rettangolo con il bordo cyan sottile e due
 * angoli tagliati in diagonale (alto-sinistra e basso-destra), come i
 * `clip-path: polygon(...)` di `.hud-panel` e `.hud-button` in styles.css.
 * Si inflata da XML con `<drawable class="com.hubpc.client.ui.HudShapeDrawable">`
 * (vedi gli attributi `hud*` in attrs.xml) oppure si costruisce da codice. */
class HudShapeDrawable() : Drawable() {

    var fill = 0
    var stroke = 0
    var strokeWidth = 0f
    var cut = 0f
    var accent = 0
    var accentWidth = 0f
    var leftBar = 0
    var leftBarWidth = 0f
    var oval = false

    private var alphaValue = 255
    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val strokePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val accentPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.SQUARE
    }
    private val barPaint = Paint().apply { style = Paint.Style.FILL }
    private val path = Path()
    private val rect = RectF()

    constructor(
        fill: Int,
        stroke: Int,
        strokeWidth: Float,
        cut: Float,
        accent: Int = 0,
        accentWidth: Float = 0f,
        leftBar: Int = 0,
        leftBarWidth: Float = 0f,
        oval: Boolean = false,
    ) : this() {
        this.fill = fill
        this.stroke = stroke
        this.strokeWidth = strokeWidth
        this.cut = cut
        this.accent = accent
        this.accentWidth = accentWidth
        this.leftBar = leftBar
        this.leftBarWidth = leftBarWidth
        this.oval = oval
    }

    override fun inflate(r: Resources, parser: XmlPullParser, attrs: AttributeSet, theme: Resources.Theme?) {
        super.inflate(r, parser, attrs, theme)
        val a = theme?.obtainStyledAttributes(attrs, R.styleable.HudShape, 0, 0)
            ?: r.obtainAttributes(attrs, R.styleable.HudShape)
        try {
            oval = a.getInt(R.styleable.HudShape_hudShape, 0) == 1
            fill = a.getColor(R.styleable.HudShape_hudFill, 0)
            stroke = a.getColor(R.styleable.HudShape_hudStroke, 0)
            strokeWidth = a.getDimension(R.styleable.HudShape_hudStrokeWidth, 0f)
            cut = a.getDimension(R.styleable.HudShape_hudCut, 0f)
            accent = a.getColor(R.styleable.HudShape_hudAccent, 0)
            accentWidth = a.getDimension(R.styleable.HudShape_hudAccentWidth, 0f)
            leftBar = a.getColor(R.styleable.HudShape_hudLeftBar, 0)
            leftBarWidth = a.getDimension(R.styleable.HudShape_hudLeftBarWidth, 0f)
        } finally {
            a.recycle()
        }
    }

    override fun draw(canvas: Canvas) {
        val b = bounds
        if (b.isEmpty) return
        val half = strokeWidth / 2f
        rect.set(b.left + half, b.top + half, b.right - half, b.bottom - half)

        fillPaint.color = fill
        fillPaint.alpha = (Color_alpha(fill) * alphaValue / 255)
        strokePaint.color = stroke
        strokePaint.strokeWidth = strokeWidth
        strokePaint.alpha = (Color_alpha(stroke) * alphaValue / 255)

        if (oval) {
            if (fill != 0) canvas.drawOval(rect, fillPaint)
            if (strokeWidth > 0f && stroke != 0) canvas.drawOval(rect, strokePaint)
            return
        }

        val c = cut.coerceAtMost(minOf(rect.width(), rect.height()) / 2f)
        path.reset()
        if (c > 0f) {
            path.moveTo(rect.left + c, rect.top)
            path.lineTo(rect.right, rect.top)
            path.lineTo(rect.right, rect.bottom - c)
            path.lineTo(rect.right - c, rect.bottom)
            path.lineTo(rect.left, rect.bottom)
            path.lineTo(rect.left, rect.top + c)
            path.close()
        } else {
            path.addRect(rect, Path.Direction.CW)
        }
        if (fill != 0) canvas.drawPath(path, fillPaint)
        if (leftBar != 0 && leftBarWidth > 0f) {
            barPaint.color = leftBar
            barPaint.alpha = (Color_alpha(leftBar) * alphaValue / 255)
            canvas.drawRect(b.left.toFloat(), rect.top + c, b.left + leftBarWidth, rect.bottom, barPaint)
        }
        if (strokeWidth > 0f && stroke != 0) canvas.drawPath(path, strokePaint)
        if (c > 0f && accent != 0 && accentWidth > 0f) {
            accentPaint.color = accent
            accentPaint.strokeWidth = accentWidth
            accentPaint.alpha = (Color_alpha(accent) * alphaValue / 255)
            canvas.drawLine(rect.left + c, rect.top, rect.left, rect.top + c, accentPaint)
            canvas.drawLine(rect.right - c, rect.bottom, rect.right, rect.bottom - c, accentPaint)
        }
    }

    private fun Color_alpha(color: Int) = (color ushr 24) and 0xFF

    override fun setAlpha(alpha: Int) {
        alphaValue = alpha
        invalidateSelf()
    }

    override fun setColorFilter(colorFilter: ColorFilter?) {
        fillPaint.colorFilter = colorFilter
        strokePaint.colorFilter = colorFilter
        accentPaint.colorFilter = colorFilter
        invalidateSelf()
    }

    @Deprecated("Deprecated in Java")
    override fun getOpacity(): Int = PixelFormat.TRANSLUCENT
}
