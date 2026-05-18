import tkinter as tk                    # GUI framework
import tkinter.messagebox as msg         # pop-up dialogs
import tkinter.ttk as ttk               # themed widgets (Notebook tabs)
import os                                # file existence + directory listing
import csv                               # structured data saving
import datetime                          # timestamps for each round result
import serial                            # UART communication with Arduino
import time                              # startup delay after serial connect
import matplotlib.pyplot as plt          # data visualization
import matplotlib.dates as mdates        # date formatting on x-axis
from collections import defaultdict      # win-rate tallying

# ============================================================
#  SERIAL CONNECTION
#  Arduino resets when Python opens the port, so we wait 2 s
#  for setup() to finish before sending any commands.
#  Change the port string to match your system:
#    Mac/Linux : "/dev/cu.usbmodem..."  or  "/dev/ttyUSB0"
#    Windows   : "COM3"  (check Device Manager)
# ============================================================

SERIAL_PORT = "COM11"
BAUD_RATE   = 9600

try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE)
    time.sleep(2)                  # wait for Arduino boot
    serial_connected = True
    print("Arduino connected on", SERIAL_PORT)
except Exception as e:
    print(f"Serial not connected: {e}")
    serial_connected = False

# ============================================================
#  FILE PATHS
#  All player records are stored as CSV files named after the
#  player.  leaderboard.txt holds ranked names, one per line.
# ============================================================

LEADERBOARD_FILE = "leaderboard.txt"
DATA_DIR = "player_data"              # subfolder keeps things tidy

os.makedirs(DATA_DIR, exist_ok=True)

def player_file(name):
    """Return the CSV path for a given player name."""
    return os.path.join(DATA_DIR, f"{name}.csv")

# ============================================================
#  WINDOW SETUP
# ============================================================

root = tk.Tk()
root.title("Reaction Game")
root.geometry("1000x1000")
root.resizable(False, False)

# ── Notebook: two tabs — Game  |  Stats ─────────────────────
notebook = ttk.Notebook(root)
notebook.pack(fill="both", expand=True, padx=10, pady=10)

game_frame  = tk.Frame(notebook)
stats_frame = tk.Frame(notebook)
notebook.add(game_frame,  text="  Game  ")
notebook.add(stats_frame, text="  Stats  ")

# ============================================================
#  GAME TAB — UI LAYOUT
# ============================================================

tk.Label(game_frame, text="Reaction Game", font=("Arial", 18, "bold")).pack(pady=10)

name_frame = tk.Frame(game_frame)
name_frame.pack()

tk.Label(name_frame, text="Player 1:", width=10, anchor="e").grid(row=0, column=0, padx=5, pady=4)
p1_entry = tk.Entry(name_frame, width=20)
p1_entry.grid(row=0, column=1, pady=4)

tk.Label(name_frame, text="Player 2:", width=10, anchor="e").grid(row=1, column=0, padx=5, pady=4)
p2_entry = tk.Entry(name_frame, width=20)
p2_entry.grid(row=1, column=1, pady=4)

status_label = tk.Label(game_frame, text="Enter names and press Start",
                        font=("Arial", 12), fg="navy")
status_label.pack(pady=8)

score_label = tk.Label(game_frame, text="Score:  0 – 0", font=("Arial", 14, "bold"))
score_label.pack(pady=4)

tk.Label(game_frame, text="Match History", font=("Arial", 10, "bold")).pack()
history_list = tk.Listbox(game_frame, width=68, height=10, font=("Courier", 9))
history_list.pack(padx=10)

tk.Label(game_frame, text="Leaderboard", font=("Arial", 10, "bold")).pack(pady=(10, 0))
leaderboard_list = tk.Listbox(game_frame, width=35, height=8, font=("Courier", 9))
leaderboard_list.pack()

btn_frame = tk.Frame(game_frame)
btn_frame.pack(pady=10)

tk.Button(btn_frame, text="Start Game",           width=18, command=lambda: start_game()).grid(row=0, column=0, padx=6, pady=4)
tk.Button(btn_frame, text="New Game",             width=18, command=lambda: new_game()).grid(row=0, column=1, padx=6, pady=4)
tk.Button(btn_frame, text="Reset Leaderboard",    width=18, command=lambda: reset_leaderboard()).grid(row=1, column=0, padx=6, pady=4)
tk.Button(btn_frame, text="Reset Player Records", width=18, command=lambda: reset_player_records()).grid(row=1, column=1, padx=6, pady=4)

# ============================================================
#  STATS TAB — UI LAYOUT
# ============================================================

tk.Label(stats_frame, text="Player Statistics", font=("Arial", 14, "bold")).pack(pady=10)

ctrl_frame = tk.Frame(stats_frame)
ctrl_frame.pack()

tk.Label(ctrl_frame, text="Player:").grid(row=0, column=0, padx=5)
stats_player_entry = tk.Entry(ctrl_frame, width=18)
stats_player_entry.grid(row=0, column=1, padx=5)

tk.Label(ctrl_frame, text="Opponent (optional):").grid(row=0, column=2, padx=5)
stats_opponent_entry = tk.Entry(ctrl_frame, width=18)
stats_opponent_entry.grid(row=0, column=3, padx=5)

btn_row = tk.Frame(stats_frame)
btn_row.pack(pady=6)

tk.Button(btn_row, text="Reaction Times Over Sessions",
          command=lambda: plot_reaction_times()).pack(side="left", padx=6)
tk.Button(btn_row, text="Win Rate vs Opponents",
          command=lambda: plot_win_rates()).pack(side="left", padx=6)
tk.Button(btn_row, text="Head-to-Head vs Opponent",
          command=lambda: plot_head_to_head()).pack(side="left", padx=6)

stats_info = tk.Label(stats_frame,
    text="Enter a player name above, then choose a chart.\n"
         "Opponent field is only required for Head-to-Head.",
    fg="gray", font=("Arial", 9), justify="center")
stats_info.pack(pady=4)

# ── Recent records preview ───────────────────────────────────
tk.Label(stats_frame, text="Recent Results", font=("Arial", 10, "bold")).pack()
stats_list = tk.Listbox(stats_frame, width=72, height=16, font=("Courier", 9))
stats_list.pack(padx=10, pady=4)

tk.Button(stats_frame, text="Load Records for Player",
          command=lambda: load_stats_preview()).pack(pady=4)

# ============================================================
#  GAME STATE
# ============================================================

p1_score    = 0
p2_score    = 0
round_number = 1
game_over   = False
game_active = False

# ============================================================
#  FILE I/O — CSV FORMAT
#  Columns: timestamp, player, opponent, result, reaction_ms
#  reaction_ms is empty for false starts and losses.
# ============================================================

CSV_HEADER = ["timestamp", "player", "opponent", "result", "reaction_ms"]

def save_result(player, opponent, result, reaction_ms=""):
    """Append one row to the player's CSV file, creating it if new."""
    path = player_file(player)
    is_new = not os.path.exists(path)

    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(CSV_HEADER)
        writer.writerow([
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            player,
            opponent,
            result,
            reaction_ms
        ])

def load_records(player):
    """Return list of dicts for every row in a player's CSV."""
    path = player_file(player)
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))

# ============================================================
#  LEADERBOARD
# ============================================================

def load_leaderboard():
    if not os.path.exists(LEADERBOARD_FILE):
        return []
    with open(LEADERBOARD_FILE) as f:
        return [line.strip() for line in f if line.strip()]

def save_leaderboard(board):
    with open(LEADERBOARD_FILE, "w") as f:
        f.write("\n".join(board) + "\n")

def update_leaderboard(winner, loser):
    board = load_leaderboard()

    for name in [winner, loser]:
        if name not in board:
            board.append(name)

    wi = board.index(winner)
    if wi > 0:                              # bubble winner up one position
        board[wi], board[wi - 1] = board[wi - 1], board[wi]

    save_leaderboard(board)
    display_leaderboard()

def display_leaderboard():
    leaderboard_list.delete(0, tk.END)
    for i, name in enumerate(load_leaderboard(), 1):
        leaderboard_list.insert(tk.END, f"  {i}.  {name}")

# ============================================================
#  RESET / DELETE
# ============================================================

def reset_leaderboard():
    if msg.askyesno("Confirm", "Clear leaderboard?"):
        if os.path.exists(LEADERBOARD_FILE):
            os.remove(LEADERBOARD_FILE)
        leaderboard_list.delete(0, tk.END)

def reset_player_records():
    if msg.askyesno("Confirm", "Delete ALL player CSV records?"):
        for f in os.listdir(DATA_DIR):
            if f.endswith(".csv"):
                os.remove(os.path.join(DATA_DIR, f))
        history_list.delete(0, tk.END)
        history_list.insert(tk.END, "All player records deleted.")

# ============================================================
#  GAME CONTROL
# ============================================================

def start_game():
    global game_active, game_over

    if game_over:
        status_label.config(text="Press New Game first.")
        return

    p1 = p1_entry.get().strip()
    p2 = p2_entry.get().strip()

    if not p1 or not p2:
        status_label.config(text="Enter both player names.")
        return

    if p1.lower() == p2.lower():
        status_label.config(text="Players must have different names.")
        return

    game_active = True
    status_label.config(text="Waiting for Arduino…", fg="navy")

    if serial_connected:
        ser.write(b"START\n")

def new_game():
    global p1_score, p2_score, round_number, game_over, game_active

    if serial_connected:
        ser.write(b"RESET\n")

    p1_score     = 0
    p2_score     = 0
    round_number = 1
    game_over    = False
    game_active  = False

    score_label.config(text="Score:  0 – 0")
    status_label.config(text="New game ready. Enter names and press Start.", fg="navy")

# ============================================================
#  SERIAL READER — polled every 50 ms via root.after()
# ============================================================

def read_serial():
    global p1_score, p2_score, round_number, game_over, game_active

    if not game_active or game_over:
        root.after(50, read_serial)
        return

    if serial_connected and ser.in_waiting:
        try:
            data = ser.readline().decode(errors="replace").strip()
        except Exception:
            root.after(50, read_serial)
            return

        print("Arduino:", data)

        p1 = p1_entry.get().strip()
        p2 = p2_entry.get().strip()
        round_finished = False

        # ── Parse Arduino message ────────────────────────────
        if data == "GO":
            status_label.config(text="⚡  GO!", fg="red")

        elif data == "TIMEOUT":
            status_label.config(text="Timeout — no press detected. Round replayed.", fg="orange")
            root.after(1500, lambda: ser.write(b"START\n"))

        elif data == "P1_FALSE":
            p2_score += 1
            _log_round(f"Round {round_number}: {p1} FALSE START → {p2} gets the point",
                       p2, p1, "WIN (false start)", p1, p2, "LOSS (false start)")
            round_finished = True

        elif data == "P2_FALSE":
            p1_score += 1
            _log_round(f"Round {round_number}: {p2} FALSE START → {p1} gets the point",
                       p1, p2, "WIN (false start)", p2, p1, "LOSS (false start)")
            round_finished = True

        elif data.startswith("P1:"):
            t_ms = int(data.split(":")[1])
            t_s  = t_ms / 1000
            p1_score += 1
            p2_score = max(0, p2_score - 1)
            _log_round(f"Round {round_number}: {p1} wins ({t_s:.3f}s)",
                    p1, p2, "WIN", p2, p1, "LOSS", t_ms)
            round_finished = True

        elif data.startswith("P2:"):
            t_ms = int(data.split(":")[1])
            t_s  = t_ms / 1000
            p2_score += 1
            p1_score = max(0, p1_score - 1)
            _log_round(f"Round {round_number}: {p2} wins ({t_s:.3f}s)",
                p2, p1, "WIN", p1, p2, "LOSS", t_ms)
            round_finished = True

        # ── Check match point ────────────────────────────────
        if p1_score >= 3:
            history_list.insert(tk.END, f"🏆  MATCH WINNER: {p1}")
            history_list.see(tk.END)
            status_label.config(text=f"🏆  {p1} wins the match!", fg="green")
            update_leaderboard(p1, p2)
            game_over   = True
            game_active = False

        elif p2_score >= 3:
            history_list.insert(tk.END, f"🏆  MATCH WINNER: {p2}")
            history_list.see(tk.END)
            status_label.config(text=f"🏆  {p2} wins the match!", fg="green")
            update_leaderboard(p2, p1)
            game_over   = True
            game_active = False

        score_label.config(text=f"Score:  {p1_score} – {p2_score}")

        # ── Auto-advance to next round ───────────────────────
        if round_finished and not game_over:
            round_number += 1
            status_label.config(text="Next round starting…", fg="navy")
            root.after(1200, lambda: ser.write(b"START\n"))

    root.after(50, read_serial)

def _log_round(history_msg,
               winner, w_opp, w_result,
               loser,  l_opp, l_result,
               reaction_ms=""):
    """Add to history listbox and save to both players' CSV files."""
    history_list.insert(tk.END, "  " + history_msg)
    history_list.see(tk.END)
    save_result(winner, w_opp, w_result, reaction_ms)
    save_result(loser,  l_opp, l_result)

# ============================================================
#  STATS TAB — LOAD PREVIEW
# ============================================================

def load_stats_preview():
    name = stats_player_entry.get().strip()
    if not name:
        msg.showinfo("Stats", "Enter a player name.")
        return

    records = load_records(name)
    stats_list.delete(0, tk.END)

    if not records:
        stats_list.insert(tk.END, f"No records found for '{name}'.")
        return

    stats_list.insert(tk.END, f"  {'Timestamp':<22} {'Opponent':<16} {'Result':<22} {'Time (ms)'}")
    stats_list.insert(tk.END, "  " + "-" * 65)

    for r in records[-50:]:                # show last 50 rows
        stats_list.insert(tk.END,
            f"  {r['timestamp']:<22} {r['opponent']:<16} {r['result']:<22} {r['reaction_ms']}")

# ============================================================
#  VISUALIZATION 1 — Reaction times across sessions
# ============================================================

def plot_reaction_times():
    name = stats_player_entry.get().strip()
    if not name:
        msg.showinfo("Stats", "Enter a player name.")
        return

    records = load_records(name)
    wins = [r for r in records if r["result"] == "WIN" and r["reaction_ms"]]

    if not wins:
        msg.showinfo("Stats", f"No winning reaction times recorded for '{name}' yet.")
        return

    times_ms = [int(r["reaction_ms"]) for r in wins]
    timestamps = [
        datetime.datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
        for r in wins
    ]

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(timestamps, times_ms, marker="o", linewidth=1.5,
            color="#2563EB", label=name)

    # Rolling average (window = 5)
    if len(times_ms) >= 5:
        rolling = [
            sum(times_ms[max(0, i-4):i+1]) / len(times_ms[max(0, i-4):i+1])
            for i in range(len(times_ms))
        ]
        ax.plot(timestamps, rolling, linewidth=2, linestyle="--",
                color="#DC2626", label="5-round avg")

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b\n%H:%M"))
    fig.autofmt_xdate()
    ax.set_xlabel("Session timestamp")
    ax.set_ylabel("Reaction time (ms)")
    ax.set_title(f"{name} — Reaction Times Over Sessions")
    ax.legend()
    ax.grid(axis="y", alpha=0.4)
    plt.tight_layout()
    plt.show()

# ============================================================
#  VISUALIZATION 2 — Win rate vs all opponents
# ============================================================

def plot_win_rates():
    name = stats_player_entry.get().strip()
    if not name:
        msg.showinfo("Stats", "Enter a player name.")
        return

    records = load_records(name)
    if not records:
        msg.showinfo("Stats", f"No records found for '{name}'.")
        return

    wins_vs   = defaultdict(int)
    losses_vs = defaultdict(int)

    for r in records:
        opp = r["opponent"]
        if r["result"].startswith("WIN"):
            wins_vs[opp] += 1
        elif r["result"].startswith("LOSS"):
            losses_vs[opp] += 1

    opponents = sorted(set(list(wins_vs.keys()) + list(losses_vs.keys())))
    win_rates = []
    totals    = []

    for opp in opponents:
        w = wins_vs[opp]
        l = losses_vs[opp]
        total = w + l
        totals.append(total)
        win_rates.append((w / total * 100) if total else 0)

    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(opponents, win_rates, color="#16A34A", edgecolor="white", width=0.5)

    for bar, total, wr in zip(bars, totals, win_rates):
        ax.text(bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 1,
                f"{wr:.0f}%\n(n={total})",
                ha="center", va="bottom", fontsize=9)

    ax.axhline(50, color="gray", linestyle="--", linewidth=1, label="50 % line")
    ax.set_ylim(0, 110)
    ax.set_xlabel("Opponent")
    ax.set_ylabel("Win rate (%)")
    ax.set_title(f"{name} — Win Rate vs Each Opponent")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.show()

# ============================================================
#  VISUALIZATION 3 — Head-to-head reaction times vs one opponent
# ============================================================

def plot_head_to_head():
    name = stats_player_entry.get().strip()
    opp  = stats_opponent_entry.get().strip()

    if not name or not opp:
        msg.showinfo("Stats", "Enter both a player name and an opponent for head-to-head.")
        return

    rec_a = [r for r in load_records(name) if r["opponent"] == opp
             and r["result"] == "WIN" and r["reaction_ms"]]
    rec_b = [r for r in load_records(opp)  if r["opponent"] == name
             and r["result"] == "WIN" and r["reaction_ms"]]

    if not rec_a and not rec_b:
        msg.showinfo("Stats", "No head-to-head win reaction times recorded yet.")
        return

    fig, ax = plt.subplots(figsize=(9, 5))

    def plot_player(records, label, color):
        if not records:
            return
        times = [int(r["reaction_ms"]) for r in records]
        ts    = [datetime.datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
                 for r in records]
        ax.scatter(ts, times, label=f"{label} (wins)", color=color, zorder=3)
        ax.plot(ts, times, color=color, linewidth=1, alpha=0.5)

    plot_player(rec_a, name, "#2563EB")
    plot_player(rec_b, opp,  "#DC2626")

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b\n%H:%M"))
    fig.autofmt_xdate()
    ax.set_xlabel("Session timestamp")
    ax.set_ylabel("Reaction time on winning rounds (ms)")
    ax.set_title(f"Head-to-Head: {name} vs {opp}")
    ax.legend()
    ax.grid(axis="y", alpha=0.4)
    plt.tight_layout()
    plt.show()

# ============================================================
#  INITIALISE AND RUN
# ============================================================

display_leaderboard()
read_serial()
root.mainloop()