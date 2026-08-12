package com.hubpc.client

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View

/** Anello di progresso disegnato a mano (nessuna libreria di grafici): usato
 * per mostrare lo spazio occupato in Pulizia, come nel riferimento MIUI. */
class DonutChartView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    var percent: Int = 0
        set(value) {
            field = value.coerceIn(0, 100)
            invalidate()
        }

    private val ringWidth = 14f * resources.displayMetrics.density
    private val bgPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = ringWidth
        strokeCap = Paint.Cap.ROUND
        color = context.getColor(R.color.surface_raised)
    }
    private val fgPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = ringWidth
        strokeCap = Paint.Cap.ROUND
        color = context.getColor(R.color.cyan)
    }
    private val rect = RectF()

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val inset = ringWidth / 2
        rect.set(inset, inset, width - inset, height - inset)
        canvas.drawArc(rect, -90f, 360f, false, bgPaint)
        if (percent > 0) {
            canvas.drawArc(rect, -90f, 360f * percent / 100f, false, fgPaint)
        }
    }
}
