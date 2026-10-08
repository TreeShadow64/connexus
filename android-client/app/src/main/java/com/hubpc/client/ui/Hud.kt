package com.hubpc.client.ui

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Typeface
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.res.ResourcesCompat
import com.hubpc.client.R

/** Mini toolkit per creare da codice gli stessi elementi HUD che nei layout
 * XML arrivano dagli stili (liste dinamiche, righe, bottoni). */
object Hud {

    fun dp(context: Context, value: Int): Int =
        (value * context.resources.displayMetrics.density + 0.5f).toInt()

    fun mono(context: Context): Typeface? = ResourcesCompat.getFont(context, R.font.share_tech_mono)

    fun hud(context: Context): Typeface? = ResourcesCompat.getFont(context, R.font.rajdhani)

    enum class Variant { NORMAL, ACCENT, DANGER }

    fun button(context: Context, label: String, variant: Variant = Variant.NORMAL, onClick: (() -> Unit)? = null): Button {
        val style = when (variant) {
            Variant.NORMAL -> R.style.Widget_Hud_Button
            Variant.ACCENT -> R.style.Widget_Hud_Button_Accent
            Variant.DANGER -> R.style.Widget_Hud_Button_Danger
        }
        return Button(context, null, 0, style).apply {
            text = label
            if (onClick != null) setOnClickListener { onClick() }
        }
    }

    fun text(context: Context, style: Int, value: String): TextView =
        TextView(context, null, 0, style).apply { text = value }

    fun eyebrow(context: Context, value: String): TextView = text(context, R.style.Text_Hud_Eyebrow, value)

    fun title(context: Context, value: String): TextView = text(context, R.style.Text_Hud_Title, value)

    fun status(context: Context, value: String): TextView = text(context, R.style.Text_Hud_Status, value)

    /** Riga di lista (.hud-row): filetto cyan a sinistra, nome in alto e
     * dettaglio in mono sotto. Il pulsante azione e' opzionale. */
    fun row(
        context: Context,
        name: String,
        detail: String? = null,
        actionLabel: String? = null,
        onClick: (() -> Unit)? = null,
        onAction: (() -> Unit)? = null,
    ): LinearLayout {
        val row = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundResource(R.drawable.bg_hud_row)
            setPadding(dp(context, 16), dp(context, 12), dp(context, 12), dp(context, 12))
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = dp(context, 8) }
        }
        val main = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        main.addView(title(context, name).apply {
            textSize = 14f
            isAllCaps = false
            letterSpacing = 0.02f
        })
        if (!detail.isNullOrEmpty()) {
            main.addView(text(context, R.style.Text_Hud_Status, detail).apply {
                textSize = 10f
                setPadding(0, dp(context, 3), 0, 0)
            })
        }
        row.addView(main)
        if (actionLabel != null) {
            row.addView(button(context, actionLabel, onClick = onAction).apply {
                minHeight = dp(context, 34)
                textSize = 10f
                setPadding(dp(context, 10), 0, dp(context, 10), 0)
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                ).apply { marginStart = dp(context, 10) }
            })
        }
        if (onClick != null) {
            row.isClickable = true
            row.isFocusable = true
            row.setOnClickListener { onClick() }
        }
        return row
    }

    /** Voce di elenco file/cartelle (.ftp-entry): nome a sinistra, dimensione o
     * freccia a destra, filetto sottile sotto. Le cartelle sono in ciano. */
    fun entryRow(context: Context, name: String, dir: Boolean, detail: String = "", onClick: () -> Unit): View {
        val container = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL }
        val row = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(context, 4), dp(context, 11), dp(context, 4), dp(context, 11))
            isClickable = true
            isFocusable = true
            setOnClickListener { onClick() }
        }
        row.addView(TextView(context).apply {
            text = if (dir) "▸  $name" else name
            typeface = mono(context)
            textSize = 12f
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.MIDDLE
            setTextColor(context.getColor(if (dir) R.color.cyan_glow else R.color.hud_white))
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        })
        val right = if (dir) "›" else detail
        if (right.isNotEmpty()) {
            row.addView(TextView(context).apply {
                text = right
                typeface = mono(context)
                textSize = 10f
                setTextColor(context.getColor(if (dir) R.color.cyan_base else R.color.text_dim))
                setPadding(dp(context, 10), 0, 0, 0)
            })
        }
        container.addView(row)
        container.addView(View(context).apply {
            setBackgroundColor(0x146FE3FF)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(context, 1))
        })
        return container
    }

    /** Messaggio informativo in un pannello (come i "nessun ... ancora" della dashboard). */
    fun emptyNote(context: Context, message: String): TextView =
        status(context, message).apply {
            setBackgroundResource(R.drawable.bg_hud_panel)
            setPadding(dp(context, 16), dp(context, 14), dp(context, 16), dp(context, 14))
        }

    fun setVisible(view: View, visible: Boolean) {
        view.visibility = if (visible) View.VISIBLE else View.GONE
    }
}

/** Dati di connessione condivisi tra le schermate: MainActivity li imposta
 * quando riesce ad autenticarsi, la barra di navigazione li passa alle altre
 * sezioni cosi' non devono ritrovarli. */
object HudSession {
    @Volatile var ip: String = ""
    @Volatile var token: String = ""

    fun applyTo(intent: Intent, from: Activity? = null): Intent {
        val sourceIp = ip.ifEmpty { from?.intent?.getStringExtra("ip").orEmpty() }
        val sourceToken = token.ifEmpty { from?.intent?.getStringExtra("token").orEmpty() }
        return intent.putExtra("ip", sourceIp).putExtra("token", sourceToken)
    }
}
