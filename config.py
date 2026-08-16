# Configuration du bot Accumulator Deriv

# --- Identifiants API ---
# À remplir dans un fichier .env réel, pas ici
DERIV_APP_ID = 1089 # App ID publique par défaut, à changer si nécessaire
DERIV_TOKEN = "" 

# --- Paramètres de Trading ---
SYMBOL = "R_100" # Volatility 100 (1s) - Ajuster selon besoin (R_10, R_25, etc.)
INITIAL_STAKE = 10.0 # Mise initiale en USD

# Liste des taux disponibles et leurs barrières associées (en décimal, ex: 0.05369% = 0.0005369)
# Format: { taux_growth: barriere_limit }
BARRIER_OPTIONS = {
    0.01: 0.0006126, # 1% growth -> ±0.06126%
    0.02: 0.0005725, # 2% growth -> ±0.05725%
    0.03: 0.0005369, # 3% growth -> ±0.05369%
    0.04: 0.0005109, # 4% growth -> ±0.05109%
    # 0.05: 0.00048xx, # À compléter si tu as la valeur exacte pour 5%
}

# --- Logique de Stratégie ---
VOLATILITY_PERIOD = 15 # Nombre de ticks pour calculer la volatilité (N)
VOLATILITY_MULTIPLIER = 2.5 # Coefficient de sécurité (K)

TARGET_TICKS_MIN = 4 # Option B: Sortie automatique après min 4 ticks
TARGET_TICKS_MAX = 5 # Option B: Sortie automatique après max 5 ticks (si TP pas atteint avant)

# Option C: Seuil de mouvement bizarre pour sortie anticipée
# Si le mouvement du prix actuel dépasse X fois la volatilité moyenne, on coupe
ABNORMAL_MOVE_THRESHOLD = 3.0 

# --- Logs ---
# Mettre à True pour voir tous les détails en local, False pour VPS (seulement erreurs/critiques)
DEBUG_MODE = True 
LOG_FILE = "trading_bot.log"
