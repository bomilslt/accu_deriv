# Configuration du bot Accumulator Deriv

# --- Identifiants API (nouvelle interface Deriv) ---
# À remplir dans un fichier .env réel, pas ici
# App ID: chaîne ALPHANUMÉRIQUE obtenue en créant une app sur https://developers.deriv.com
# Token: Personal Access Token au format pat_... (affiché une seule fois à sa création)
DERIV_APP_ID = ""
DERIV_TOKEN = ""

# --- Paramètres de Trading ---
# Ces 3 paramètres sont surchargeables via le .env:
#   DERIV_SYMBOL, DERIV_STAKE, DERIV_ACCOUNT_TYPE
SYMBOL = "R_100" # Volatility 100 (1s) - Ajuster selon besoin (R_10, R_25, etc.)
INITIAL_STAKE = 10.0 # Mise initiale en USD

ACCOUNT_TYPE = "demo" # "demo" ou "real" - compte à utiliser (démo par défaut, plus sûr)

# Liste des taux disponibles et leurs barrières associées (en décimal, ex: 0.05369% = 0.0005369)
# Format: { taux_growth: barriere_limit }
# Valeurs exactes récupérées depuis le shortcode des contrats ACCU (R_100, API 2026)
BARRIER_OPTIONS = {
    0.01: 0.000612552024, # 1% growth -> ±0.06126%
    0.02: 0.000572524639, # 2% growth -> ±0.05725%
    0.03: 0.000536927765, # 3% growth -> ±0.05369%
    # 0.04: 0.000510864957, # 4% growth -> ±0.05109%
    # 0.05: 0.000486253948, # 5% growth -> ±0.04863%
}

# --- Logique de Stratégie ---
VOLATILITY_PERIOD = 20 # Fenêtre d'analyse de la volatilité (>= 20 recommandé)
VOLATILITY_MULTIPLIER = 2.5 # Coefficient de sécurité (K)

COOLDOWN_TICKS = 10 # Ticks d'attente après une clôture avant de ré-entrer
MAX_POSITION_SECONDS = 30 # Garde-fou: vente forcée si la position dure trop (ticks bloqués)

TARGET_TICKS_MIN = 4 # Option B: Sortie automatique après min 4 ticks
TARGET_TICKS_MAX = 5 # Option B: Sortie automatique après max 5 ticks (si TP pas atteint avant)

# Option C: Seuil de mouvement bizarre pour sortie anticipée
# Si le mouvement du prix actuel dépasse X fois la volatilité moyenne, on coupe
ABNORMAL_MOVE_THRESHOLD = 3.0 

# --- Logs ---
# Mettre à True pour voir tous les détails en local, False pour VPS (seulement erreurs/critiques)
DEBUG_MODE = False 
LOG_FILE = "trading_bot.log"
