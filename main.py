import os
import discord
from discord.ext import commands
from discord.ui import Button, View

# ★ ランキング機能に必要なライブラリは削除済み

# --- 常時起動に必要なライブラリ (Flask/Thread) ---
from threading import Thread 
from flask import Flask 
import logging 
import time

# ★★★★★ ここを付与したいロールのIDに書き換えてください ★★★★★
# 認証完了時に付与するロールのID
ROLE_ID = 1449020772591996989 
# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★

# ----------------------------------------------------
# --- 0. 利用規約のコンテンツ定義 (共通化) ---
# ----------------------------------------------------
RULE_CONTENT = (
    "**【サーバー利用規約】**\n\n"
    "当サーバーをご利用いただきありがとうございます。快適なコミュニティ維持のため、以下のルールを遵守してください。\n"
    "---"
    "1. **禁止行為**: 他者を誹謗中傷する発言、差別的な表現、過度なスパム行為を固く禁じます。\n"
    "2. **個人情報**: 他のユーザーの個人情報を許可なく公開することを禁じます。\n"
    "3. **著作権**: 著作権や肖像権を侵害するコンテンツの投稿を禁じます。\n"
    "4. **チャットマナー**: 不必要な大文字多用、連続投稿は控えてください。\n"
    "5. **宣伝行為**: 許可されていない外部サイト、サーバー、SNSの宣伝行為は禁止します。\n"
    "6. **アカウント**: 認証は一人一口座とし、複数のアカウントで認証を行うことを禁止します。\n"
    "7. **販売行為**: 販売は禁止とします。\n"
    "8. **その他**: その他、公序良俗に反する行為や、運営が不適切と判断した行為を禁止します。\n"
    "9. **最後に**: 改めてになりますが、上記のルールは必ず守ってください。違反する場合は適切な対応をとらせていただきます。\n"
    "---"
)


# --- Secretsからのトークン安全読み込み ---
try:
    TOKEN = os.environ['BOT_TOKEN']
except KeyError:
    print("🚨 エラー: 'BOT_TOKEN' がReplit Secretsに設定されていません。")
    exit()

# --- インテンツの設定 ---
intents = discord.Intents.default()
intents.members = True 
intents.message_content = True 

bot = commands.Bot(command_prefix='!', intents=intents)

# ----------------------------------------------------
# --- 1. 常時起動 Webサーバーの定義 (安定化対策適用) ---
# ----------------------------------------------------

app = Flask(__name__)
# Flaskのログ出力を最小限に抑える設定
app.logger.disabled = True
logging.getLogger('werkzeug').disabled = True 

@app.route('/')
def home():
    return "Bot is alive! Running on port " + str(os.environ.get('PORT', 5000))

def run_server():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def keep_alive():
    server_thread = Thread(target=run_server)
    server_thread.start()

# ----------------------------------------------------
# --- 2. Discord BotのViewとコマンドの定義 ---
# ----------------------------------------------------

# --- 2-1. 同意ボタン（認証機能） ---
class AgreeView(View):
    def __init__(self):
        super().__init__(timeout=None) 

    @discord.ui.button(label="同意します", style=discord.ButtonStyle.green, custom_id="agree_button")
    async def agree_callback(self, interaction: discord.Interaction, button: Button):
        role = interaction.guild.get_role(ROLE_ID)
        user = interaction.user

        if role is None:
            await interaction.response.send_message("❌ エラー: 設定された認証用ロールが見つかりません。", ephemeral=True)
            return

        if role in user.roles:
            await interaction.response.send_message("🔔 すでに認証済みです！", ephemeral=True)
        else:
            try:
                await user.add_roles(role)
                await interaction.response.send_message(f"✅ 認証が完了しました！ **{role.name}** ロールを付与しました。", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message(
                    "❌ エラー: Botにロールを付与する権限がありません。"
                    "（Botのロールが、付与対象ロールより**上**にあるか確認してください）", 
                    ephemeral=True
                )
            except Exception as e:
                print(f"ロール付与中に予期せぬエラーが発生: {e}")
                await interaction.response.send_message("❌ 予期せぬエラーが発生しました。運営にご連絡ください。", ephemeral=True)


# --- 2-2. 認証開始ボタン（認証機能） ---
class AuthStartView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="認証を始める", style=discord.ButtonStyle.blurple, custom_id="start_auth")
    async def start_callback(self, interaction: discord.Interaction, button: Button):

        full_rule_content = RULE_CONTENT + (
            "\n（ルールの最後までスクロールしてください）\n"
            "...\n...\n...\n...\n...\n...\n...\n"
            "---"
            "**上記の内容をすべて読み、理解し、同意する場合は下の「同意します」ボタンを押して認証を完了してください。**"
        )

        embed = discord.Embed(
            title="📜 利用規約の確認と同意", 
            description=full_rule_content, 
            color=discord.Color.blue()
        )

        await interaction.response.send_message(
            content="以下の内容に同意しますか？",
            embed=embed,
            view=AgreeView(), 
            ephemeral=True 
        )


# --- 2-3. 利用規約をチャンネルに表示するコマンド（管理者限定） ---
@bot.command()
@commands.has_permissions(administrator=True) 
async def post_rules(ctx):
    """管理者のみが実行可能。利用規約を埋め込みメッセージとして現在のチャンネルに表示します。"""
    embed = discord.Embed(
        title="📜 サーバー利用規約", 
        description=RULE_CONTENT + "\n\n**同意と認証は、認証チャンネルにあるパネルから行ってください。**", 
        color=discord.Color.dark_red() 
    )
    await ctx.send(embed=embed)
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        await ctx.send("✅ 利用規約を現在のチャンネルに投稿しました。", delete_after=5)


# --- 2-4. 認証パネルを設置するコマンド（管理者限定） ---
@bot.command()
@commands.has_permissions(administrator=True) 
async def setup_auth(ctx):
    """管理者のみが実行可能。認証パネルを現在のチャンネルに設置します。"""
    if ROLE_ID == 0 or ctx.guild.get_role(ROLE_ID) is None:
         await ctx.send("❌ エラー: コード内の `ROLE_ID` が無効か、サーバーに存在しません。", delete_after=10)
         return
    embed = discord.Embed(title="🔔 メンバー認証エリア 🔔", 
                          description="当サーバーへの参加を続けるには、下の「認証を始める」ボタンを押して利用規約に同意してください。", 
                          color=discord.Color.gold())
    await ctx.send(embed=embed, view=AuthStartView())
    await ctx.send("認証パネルを設置しました。チャンネルを確認してください。", delete_after=5)


# --- 2-5. 対応状況パネルの View 定義（管理者のみ操作可能） ---
class StatusView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.current_status_label = "対応可能" 
        self.current_status_color = discord.Color.green()

    # ヘルパー関数：管理者かどうかチェック
    def is_admin(self, interaction: discord.Interaction):
        return interaction.user.guild_permissions.administrator

    # パネル更新関数
    async def update_panel(self, interaction: discord.Interaction):
        new_embed = discord.Embed(
            title="現在の対応状況", 
            description=f"現在のステータs: **{self.current_status_label}**",
            color=self.current_status_color
        )
        new_embed.set_footer(text=f"最終更新者: {interaction.user.display_name} | {discord.utils.utcnow().strftime('%Y/%m/%d %H:%M:%S')} (UTC)")

        await interaction.message.edit(embed=new_embed, view=self)
        await interaction.response.send_message(f"✅ ステータスを「{self.current_status_label}」に更新しました。", ephemeral=True)

    @discord.ui.button(label="対応可能", style=discord.ButtonStyle.green, custom_id="status_available")
    async def available_callback(self, interaction: discord.Interaction, button: Button):
        if not self.is_admin(interaction):
            await interaction.response.send_message("❌ この操作は管理者のみ可能です。", ephemeral=True)
            return
        self.current_status_label = "対応可能"
        self.current_status_color = discord.Color.green()
        await self.update_panel(interaction)

    @discord.ui.button(label="対応遅延", style=discord.ButtonStyle.blurple, custom_id="status_delayed")
    async def delayed_callback(self, interaction: discord.Interaction, button: Button):
        if not self.is_admin(interaction):
            await interaction.response.send_message("❌ この操作は管理者のみ可能です。", ephemeral=True)
            return
        self.current_status_label = "対応遅延"
        self.current_status_color = discord.Color.blue()
        await self.update_panel(interaction)

    @discord.ui.button(label="対応不可", style=discord.ButtonStyle.red, custom_id="status_unavailable")
    async def unavailable_callback(self, interaction: discord.Interaction, button: Button):
        if not self.is_admin(interaction):
            await interaction.response.send_message("❌ この操作は管理者のみ可能です。", ephemeral=True)
            return
        self.current_status_label = "対応不可"
        self.current_status_color = discord.Color.red()
        await self.update_panel(interaction)


# --- 2-6. 対応状況パネル設置コマンド（管理者限定） ---
@bot.command()
@commands.has_permissions(administrator=True) 
async def setup_status(ctx):
    """管理者のみが実行可能。現在のチャンネルに対応状況パネルを設置します。"""
    initial_view = StatusView()
    initial_embed = discord.Embed(
        title="現在の対応状況", 
        description=f"現在のステータス: **{initial_view.current_status_label}**",
        color=initial_view.current_status_color
    )
    initial_embed.set_footer(text="ステータスを更新するにはボタンを押してください")
    await ctx.send(embed=initial_embed, view=initial_view)
    await ctx.send("✅ 対応状況パネルを設置しました。", delete_after=5)
    try:
        await ctx.message.delete()
    except:
        pass

# ----------------------------------------------------
# --- 3. ランキング機能のロジック (削除済み) ---
# ----------------------------------------------------
# ランキング機能（get_vocaloard_ranking() 関数と !ranking コマンド）は削除されました。


# --- 2-7. Bot起動時の処理と View の再登録 ---
@bot.event
async def on_ready():
    print(f'✅ ログインしました: {bot.user.name}')
    # すべての View を再登録して再起動後もボタンが機能するようにする
    bot.add_view(AuthStartView())
    bot.add_view(AgreeView())
    bot.add_view(StatusView())


# ----------------------------------------------------
# --- 4. 実行ブロック (ファイルの末尾に配置) ---
# ----------------------------------------------------

if __name__ == '__main__':
    # 1. Webサーバーをバックグラウンドで起動
    keep_alive()
    print("🌐 Webサーバー (Keep-Alive機能) を起動しました。")

    # 2. Discord Botを自動再起動ループで実行
    while True:
        try:
            time.sleep(1) 
            bot.run(TOKEN)
        except discord.errors.LoginFailure:
            print("\n\n🚨 致命的なエラー: Botトークンが無効または見つかりません。ReplitのSecretsを確認してください。\n")
            break
        except Exception as e:
            print(f"\n\n🚨 予期せぬエラーによりBotが終了しました: {e}。5秒後に再起動を試みます。\n")
            time.sleep(5)
            continue
