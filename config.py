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
VOLATILITY_PERIOD = 25 # Fenêtre d'analyse de la volatilité (>= 20 recommandé)
VOLATILITY_MULTIPLIER = 3 # Coefficient de sécurité (K)

COOLDOWN_TICKS = 15 # Ticks d'attente après une clôture avant de ré-entrer
MAX_POSITION_SECONDS = 10 # Garde-fou: vente forcée si la position dure trop (ticks bloqués)

TARGET_TICKS_MIN = 4 # Option B: Sortie automatique après min 4 ticks
TARGET_TICKS_MAX = 5 # Option B: Sortie automatique après max 5 ticks (si TP pas atteint avant)

# Option C: Seuil de mouvement bizarre pour sortie anticipée
# Si le mouvement du prix actuel dépasse X fois la volatilité moyenne, on coupe
ABNORMAL_MOVE_THRESHOLD = 2.0 

# --- Filtre de tendance (condition d'entrée) ---
# Empêche d'acheter "à l'instant T" dès la fin du cooldown: exige que le marché
# marche régulièrement dans une direction (micro-tendance) avant d'entrer.
# Un ACCU gagne quand le prix fait des petits pas réguliers (même direction),
# pas quand il oscille en va-et-vient (risque de casser la barrière).
TREND_FILTER_ENABLED = True   # Activer le filtre de tendance avant chaque achat
TREND_WINDOW = 20             # Nb de ticks analysés pour le momentum directionnel
TREND_DIRECTIONALITY = 0.75    # Fraction de ticks dans une même direction requise (0.5-1.0)
TREND_MAX_WAIT_TICKS = 0      # 0 = attendre indéfiniment; sinon fallback après X ticks sans signal

# --- Confirmation de calme avant ré-entrée ---
# Après une clôture, le bot ne se relance pas au premier tick "sûr": il ré-étudie
# le marché pour vérifier que le calme est réel ET soutenu.
#   1) REOBSERVE_TICKS: nb de ticks FRAIS (post-clôture) à collecter avant d'évaluer
#      (garantit une fenêtre de volatilité 100% fraîche).
#   2) CALM_CONFIRM_TICKS: nb de ticks CONSÉCUTIFS "calmes" requis — un tick trop
#      brutal remet le compteur à zéro (on ne relance pas juste après un à-coup).
REOBSERVE_TICKS = 30        # ticks frais à collecter après une clôture avant d'entrer
CALM_CONFIRM_TICKS = 5      # ticks consécutifs "calmes" requis avant l'entrée
CALM_MAX_TICK_MOVE = 0.0  # variat. max par tick jugée "calme" (0 = auto: barrière la plus large)

# --- Logs ---
# Mettre à True pour voir tous les détails en local, False pour VPS (seulement erreurs/critiques)
DEBUG_MODE = False
LOG_FILE = "trading_bot.log"
