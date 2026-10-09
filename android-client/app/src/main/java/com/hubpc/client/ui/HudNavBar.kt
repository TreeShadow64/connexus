package com.hubpc.client.ui

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.res.ColorStateList
import android.util.AttributeSet
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.widget.TextViewCompat
import com.hubpc.client.FilesActivity
import com.hubpc.client.FindDeviceActivity
import com.hubpc.client.MainActivity
import com.hubpc.client.R
import com.hubpc.client.SystemActivity
import com.hubpc.client.TvRemoteActivity

/** Navigazione in basso con le stesse sezioni della sidebar della dashboard
 * PC (`.app-nav`): Overview, TV, File & Rete, Impostazioni, Trova Dispositivo.
 * La voce attiva ha il filetto ciano e lo sfondo leggero come `.app-nav-item.active`. */
class HudNavBar @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : LinearLayout(context, attrs) {

    private data class Item(val key: Int, val label: String, val icon: Int, val target: Class<*>)

    private val items = listOf(
        Item(1, "OVERVIEW", R.drawable.ic_nav_overview, MainActivity::class.java),
        Item(2, "TV", R.drawable.ic_nav_tv, TvRemoteActivity::class.java),
        Item(3, "FILE & RETE", R.drawable.ic_nav_files, FilesActivity::class.java),
        Item(4, "IMPOSTAZIONI", R.drawable.ic_nav_settings, SystemActivity::class.java),
        Item(5, "TROVA", R.drawable.ic_nav_find, FindDeviceActivity::class.java),
    )

    init {
        orientation = VERTICAL
        val active = attrs?.let {
            val a = context.obtainStyledAttributes(it, R.styleable.HudNavBar)
            try {
                a.getInt(R.styleable.HudNavBar_hudActive, 0)
            } finally {
                a.recycle()
            }
        } ?: 0
        fun dp(v: Int) = Hud.dp(context, v)

        setBackgroundColor(0xF2030608.toInt())
        addView(View(context).apply {
            setBackgroundColor(context.getColor(R.color.border_soft))
            layoutParams = LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(1))
        })
        val row = LinearLayout(context).apply { orientation = HORIZONTAL }
        for (item in items) {
            val selected = item.key == active
            val color = context.getColor(if (selected) R.color.cyan_glow else R.color.text_dim)
            val cell = LinearLayout(context).apply {
                orientation = VERTICAL
                gravity = Gravity.CENTER_HORIZONTAL
                layoutParams = LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                setPadding(dp(2), 0, dp(2), dp(6))
                if (selected) setBackgroundColor(0x146FE3FF)
                isClickable = true
                isFocusable = true
                setOnClickListener { go(item) }
            }
            cell.addView(View(context).apply {
                setBackgroundColor(if (selected) context.getColor(R.color.cyan_glow) else 0)
                layoutParams = LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(2))
            })
            val iconFrame = android.widget.FrameLayout(context).apply {
                layoutParams = LayoutParams(dp(26), dp(20)).apply { topMargin = dp(7) }
            }
            iconFrame.addView(ImageView(context).apply {
                setImageResource(item.icon)
                imageTintList = ColorStateList.valueOf(color)
                layoutParams = android.widget.FrameLayout.LayoutParams(dp(20), dp(20), Gravity.START)
            })
            if (item.key == 4) {
                // puntino "aggiornamento disponibile", come sulla dashboard PC
                updateDot = View(context).apply {
                    setBackgroundResource(R.drawable.bg_dot_warn)
                    visibility = if (AppUpdater.updateAvailable) VISIBLE else GONE
                    layoutParams = android.widget.FrameLayout.LayoutParams(dp(8), dp(8), Gravity.END or Gravity.TOP)
                }
                iconFrame.addView(updateDot)
            }
            cell.addView(iconFrame)
            val label = TextView(context, null, 0, R.style.Text_Hud_Eyebrow).apply {
                text = item.label
                gravity = Gravity.CENTER
                maxLines = 1
                setTextColor(color)
                letterSpacing = 0.06f
                layoutParams = LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                    .apply { topMargin = dp(3) }
            }
            TextViewCompat.setAutoSizeTextTypeUniformWithConfiguration(
                label, 7, 9, 1, android.util.TypedValue.COMPLEX_UNIT_SP,
            )
            cell.addView(label)
            row.addView(cell)
        }
        addView(row)
    }

    private var updateDot: View? = null
    private val onUpdateChange: () -> Unit = {
        updateDot?.visibility = if (AppUpdater.updateAvailable) VISIBLE else GONE
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        AppUpdater.listeners.add(onUpdateChange)
        onUpdateChange()
    }

    override fun onDetachedFromWindow() {
        AppUpdater.listeners.remove(onUpdateChange)
        super.onDetachedFromWindow()
    }

    private fun go(item: Item) {
        val activity = context as? Activity ?: return
        if (activity::class.java == item.target) return
        if (item.target == MainActivity::class.java) {
            // l'Overview e' sempre sotto nello stack: basta chiudere la sezione corrente
            if (activity is MainActivity) return
            activity.finish()
            return
        }
        activity.startActivity(HudSession.applyTo(Intent(activity, item.target), activity))
        if (activity !is MainActivity) activity.finish()
        activity.overridePendingTransition(0, 0)
    }
}
