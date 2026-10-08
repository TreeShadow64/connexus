package com.hubpc.client.ui

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.graphics.Path
import android.util.AttributeSet
import android.view.View
import android.view.animation.LinearInterpolator
import com.hubpc.client.R

/** Anelli decorativi dell'Overview (`.hud-ring-wrap` della dashboard PC):
 * cerchio esterno tratteggiato che ruota lentamente con due frecce verdi,
 * anello puntinato interno che ruota al contrario e cerchio fisso piu'
 * piccolo. L'orologio e le etichette vanno sovrapposti dal layout. */
class HudRingView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    private val cyanGlow = context.getColor(R.color.cyan_glow)
    private val cyanBase = context.getColor(R.color.cyan_base)
    private val green = context.getColor(R.color.hud_green)

    private val faintPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        color = context.getColor(R.color.cyan_faint)
    }
    private val dashPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        color = cyanGlow
        alpha = 178
    }
    private val dotPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        color = cyanBase
    }
    private val innerPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        color = 0x596FE3FF
    }
    private val arrowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = green
    }

    private var outerAngle = 0f
    private var innerAngle = 0f
    private val arrow = Path()
    private var outerAnimator: ValueAnimator? = null
    private var innerAnimator: ValueAnimator? = null

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        outerAnimator = ValueAnimator.ofFloat(0f, 360f).apply {
            duration = 40_000
            repeatCount = ValueAnimator.INFINITE
            interpolator = LinearInterpolator()
            addUpdateListener { outerAngle = it.animatedValue as Float; invalidate() }
            start()
        }
        innerAnimator = ValueAnimator.ofFloat(360f, 0f).apply {
            duration = 60_000
            repeatCount = ValueAnimator.INFINITE
            interpolator = LinearInterpolator()
            addUpdateListener { innerAngle = it.animatedValue as Float }
            start()
        }
    }

    override fun onDetachedFromWindow() {
        outerAnimator?.cancel()
        innerAnimator?.cancel()
        super.onDetachedFromWindow()
    }

    override fun onDraw(canvas: Canvas) {
        val size = minOf(width, height).toFloat()
        if (size <= 0f) return
        val cx = width / 2f
        val cy = height / 2f
        val unit = size / 260f // il disegno originale e' in un viewBox 260x260

        // cerchio esterno: guida sottile + tratteggio che ruota, frecce verdi
        canvas.save()
        canvas.rotate(outerAngle, cx, cy)
        faintPaint.strokeWidth = unit
        canvas.drawCircle(cx, cy, 118f * unit, faintPaint)
        dashPaint.strokeWidth = 2f * unit
        dashPaint.pathEffect = DashPathEffect(
            floatArrayOf(40f * unit, 8f * unit, 4f * unit, 8f * unit, 90f * unit, 8f * unit, 4f * unit, 8f * unit), 0f,
        )
        canvas.drawCircle(cx, cy, 118f * unit, dashPaint)
        arrow.reset()
        arrow.moveTo(cx, cy - 126f * unit)
        arrow.lineTo(cx + 6f * unit, cy - 114f * unit)
        arrow.lineTo(cx - 6f * unit, cy - 114f * unit)
        arrow.close()
        canvas.drawPath(arrow, arrowPaint)
        arrow.reset()
        arrow.moveTo(cx, cy + 126f * unit)
        arrow.lineTo(cx + 6f * unit, cy + 114f * unit)
        arrow.lineTo(cx - 6f * unit, cy + 114f * unit)
        arrow.close()
        canvas.drawPath(arrow, arrowPaint)
        canvas.restore()

        // anello puntinato interno, in senso inverso
        canvas.save()
        canvas.rotate(innerAngle, cx, cy)
        dotPaint.strokeWidth = unit
        dotPaint.pathEffect = DashPathEffect(floatArrayOf(2f * unit, 6f * unit), 0f)
        canvas.drawCircle(cx, cy, 90f * unit, dotPaint)
        canvas.restore()

        // cerchio fisso piu' piccolo
        innerPaint.strokeWidth = unit
        canvas.drawCircle(cx, cy, 66f * unit, innerPaint)
    }
}
