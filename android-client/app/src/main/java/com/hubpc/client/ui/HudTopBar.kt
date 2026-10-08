package com.hubpc.client.ui

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Color
import android.text.SpannableString
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.util.AttributeSet
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import com.hubpc.client.R
import com.hubpc.client.SystemActivity

/** Barra superiore della dashboard PC (`.app-topbar`): logo, titolo "CONNEXUS
 * // ..." con il nome del dispositivo sotto in piccolo, stato e ingranaggio.
 * Nelle sotto-schermate al posto del logo c'e' la freccia indietro. */
class HudTopBar @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : LinearLayout(context, attrs) {

    enum class Mode { HOME, SECTION, SUB }

    private val titleView: TextView
    private val subtitleView: TextView
    private val statusLabel: TextView
    private val statusDot: View
    private val statusBlock: LinearLayout
    private val helpBadge: TextView
    private val gearButton: FrameLayout
    private var mode = Mode.SECTION
    private var baseTitle = ""

    var onHelp: (() -> Unit)? = null

    init {
        orientation = VERTICAL
        val density = resources.displayMetrics.density
        fun dp(v: Int) = (v * density + 0.5f).toInt()

        val row = LinearLayout(context).apply {
            orientation = HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(14), dp(10), dp(14), dp(10))
        }

        // indietro (sotto-schermate) oppure logo (sezioni principali)
        val back = ImageView(context).apply {
            setImageResource(R.drawable.ic_hud_back)
            imageTintList = ColorStateList.valueOf(context.getColor(R.color.cyan_glow))
            setBackgroundResource(R.drawable.bg_icon_button)
            setPadding(dp(8), dp(8), dp(8), dp(8))
            layoutParams = LayoutParams(dp(38), dp(38)).apply { marginEnd = dp(12) }
            contentDescription = "Indietro"
            isClickable = true
            isFocusable = true
            setOnClickListener { (context as? androidx.activity.ComponentActivity)?.onBackPressedDispatcher?.onBackPressed() }
        }
        val logo = ImageView(context).apply {
            setImageResource(R.drawable.ic_hud_logo)
            layoutParams = LayoutParams(dp(30), dp(30)).apply { marginEnd = dp(12) }
        }

        val titleBlock = LinearLayout(context).apply {
            orientation = VERTICAL
            layoutParams = LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        titleView = TextView(context, null, 0, R.style.Text_Hud_Title).apply {
            textSize = 16f
            maxLines = 1
        }
        subtitleView = TextView(context, null, 0, R.style.Text_Hud_Eyebrow).apply {
            textSize = 9f
            maxLines = 1
        }
        titleBlock.addView(titleView)
        titleBlock.addView(subtitleView)

        statusLabel = TextView(context, null, 0, R.style.Text_Hud_Mono).apply {
            textSize = 11f
            setTextColor(context.getColor(R.color.hud_white))
            text = "OFFLINE"
        }
        statusDot = View(context).apply {
            setBackgroundResource(R.drawable.bg_dot_off)
            layoutParams = LayoutParams(dp(7), dp(7)).apply { marginEnd = dp(6) }
        }
        statusBlock = LinearLayout(context).apply {
            orientation = VERTICAL
            gravity = Gravity.END
            layoutParams = LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                .apply { marginEnd = dp(12) }
        }
        statusBlock.addView(TextView(context, null, 0, R.style.Text_Hud_Eyebrow).apply {
            textSize = 8f
            text = "STATO"
            gravity = Gravity.END
        })
        statusBlock.addView(LinearLayout(context).apply {
            orientation = HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL or Gravity.END
            addView(statusDot)
            addView(statusLabel)
        })

        helpBadge = TextView(context, null, 0, R.style.HelpBadge).apply {
            text = "?"
            layoutParams = LayoutParams(dp(26), dp(26)).apply { marginEnd = dp(10) }
            setOnClickListener { onHelp?.invoke() }
        }

        gearButton = FrameLayout(context).apply {
            setBackgroundResource(R.drawable.bg_icon_button)
            layoutParams = LayoutParams(dp(38), dp(38))
            isClickable = true
            isFocusable = true
            contentDescription = "Impostazioni"
            addView(ImageView(context).apply {
                setImageResource(R.drawable.ic_nav_settings)
                imageTintList = ColorStateList.valueOf(context.getColor(R.color.cyan_base))
                layoutParams = FrameLayout.LayoutParams(dp(22), dp(22), Gravity.CENTER)
            })
            setOnClickListener {
                val activity = context as? Activity ?: return@setOnClickListener
                activity.startActivity(
                    HudSession.applyTo(Intent(activity, SystemActivity::class.java), activity)
                )
            }
        }

        row.addView(back)
        row.addView(logo)
        row.addView(titleBlock)
        row.addView(statusBlock)
        row.addView(helpBadge)
        row.addView(gearButton)

        addView(row)
        addView(View(context).apply {
            setBackgroundColor(context.getColor(R.color.border_soft))
            layoutParams = LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(1))
        })

        var helpOn = false
        if (attrs != null) {
            val a = context.obtainStyledAttributes(attrs, R.styleable.HudTopBar)
            try {
                baseTitle = a.getString(R.styleable.HudTopBar_hudTitle).orEmpty()
                subtitleView.text = a.getString(R.styleable.HudTopBar_hudSubtitle).orEmpty()
                mode = when (a.getInt(R.styleable.HudTopBar_hudMode, 1)) {
                    0 -> Mode.HOME
                    2 -> Mode.SUB
                    else -> Mode.SECTION
                }
                helpOn = a.getBoolean(R.styleable.HudTopBar_hudHelp, false)
            } finally {
                a.recycle()
            }
        }
        val isSettings = context is SystemActivity
        back.visibility = if (mode == Mode.SUB) VISIBLE else GONE
        logo.visibility = if (mode == Mode.SUB) GONE else VISIBLE
        statusBlock.visibility = if (mode == Mode.HOME) VISIBLE else GONE
        helpBadge.visibility = if (helpOn) VISIBLE else GONE
        gearButton.visibility = if (mode == Mode.SUB || isSettings) GONE else VISIBLE
        render()
    }

    private fun render() {
        // nel titolo la parte dopo "//" e' ciano come sulla dashboard PC
        val full = if (mode == Mode.HOME) "CONNEXUS // PHONE" else baseTitle.uppercase()
        val span = SpannableString(full)
        val idx = full.indexOf("//")
        if (idx >= 0) {
            span.setSpan(
                ForegroundColorSpan(context.getColor(R.color.cyan_glow)),
                idx, full.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE,
            )
        }
        titleView.text = span
    }

    fun setTitle(title: String) {
        baseTitle = title
        render()
    }

    fun setSubtitle(text: String) {
        subtitleView.text = text.uppercase()
    }

    fun setOnline(online: Boolean, label: String = if (online) "ONLINE" else "OFFLINE") {
        statusDot.setBackgroundResource(if (online) R.drawable.bg_dot_online else R.drawable.bg_dot_off)
        statusLabel.text = label.uppercase()
        statusLabel.setTextColor(if (online) context.getColor(R.color.hud_white) else context.getColor(R.color.hud_red))
    }

    @Suppress("unused")
    private fun transparent() = Color.TRANSPARENT
}
