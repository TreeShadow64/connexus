package com.hubpc.client

import android.content.Intent
import android.os.Bundle
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import com.google.android.gms.auth.api.signin.GoogleSignIn
import com.google.android.gms.auth.api.signin.GoogleSignInOptions
import com.google.android.gms.common.api.ApiException
import com.google.firebase.auth.FirebaseAuth
import com.google.firebase.auth.GoogleAuthProvider
import com.hubpc.client.databinding.ActivityLoginBinding

/** Login con Google o email/password (Firebase Authentication). L'accesso
 * riguarda solo l'identità dell'utente: IP/token del PC restano locali e si
 * configurano subito dopo, in OnboardingActivity. */
class LoginActivity : AppCompatActivity() {

    private lateinit var binding: ActivityLoginBinding
    private lateinit var auth: FirebaseAuth

    private val googleSignInLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val task = GoogleSignIn.getSignedInAccountFromIntent(result.data)
        try {
            val account = task.getResult(ApiException::class.java)
            val credential = GoogleAuthProvider.getCredential(account.idToken, null)
            auth.signInWithCredential(credential)
                .addOnSuccessListener { proceedAfterLogin() }
                .addOnFailureListener { showStatus("Accesso Google fallito: ${it.message}") }
        } catch (e: ApiException) {
            showStatus("Accesso Google annullato o fallito")
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityLoginBinding.inflate(layoutInflater)
        setContentView(binding.root)
        auth = FirebaseAuth.getInstance()

        if (auth.currentUser != null) {
            proceedAfterLogin()
            return
        }

        binding.buttonGoogleSignIn.setOnClickListener { startGoogleSignIn() }
        binding.buttonLogin.setOnClickListener { emailSignIn(register = false) }
        binding.buttonRegister.setOnClickListener { emailSignIn(register = true) }
    }

    private fun startGoogleSignIn() {
        val webClientIdRes = resources.getIdentifier("default_web_client_id", "string", packageName)
        if (webClientIdRes == 0) {
            showStatus("Accesso Google non ancora configurato lato server — usa email o riprova più tardi")
            return
        }
        val options = GoogleSignInOptions.Builder(GoogleSignInOptions.DEFAULT_SIGN_IN)
            .requestIdToken(getString(webClientIdRes))
            .requestEmail()
            .build()
        val client = GoogleSignIn.getClient(this, options)
        googleSignInLauncher.launch(client.signInIntent)
    }

    private fun emailSignIn(register: Boolean) {
        val email = binding.editEmail.text.toString().trim()
        val password = binding.editPassword.text.toString()
        if (email.isEmpty() || password.isEmpty()) {
            showStatus("Inserisci email e password")
            return
        }
        showStatus(if (register) "Registrazione in corso..." else "Accesso in corso...")
        val task = if (register) {
            auth.createUserWithEmailAndPassword(email, password)
        } else {
            auth.signInWithEmailAndPassword(email, password)
        }
        task.addOnSuccessListener { proceedAfterLogin() }
            .addOnFailureListener { showStatus(it.message ?: "Accesso fallito") }
    }

    private fun proceedAfterLogin() {
        val prefs = getSharedPreferences(HubApplication.PREFS_NAME, MODE_PRIVATE)
        val onboarded = prefs.getBoolean("onboarded", false)
        val target = if (onboarded) MainActivity::class.java else OnboardingActivity::class.java
        startActivity(Intent(this, target))
        finish()
    }

    private fun showStatus(message: String) {
        binding.textLoginStatus.text = message
    }
}
