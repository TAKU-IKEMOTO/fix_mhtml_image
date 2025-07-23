# -*- coding: utf-8 -*-
# ライブラリのインポート
import sys
import re
import os
import uuid
import quopri

def fix_mhtml_image_references(input_file: str):
    """
    MHTMLファイル内の壊れた画像参照を完全に修正するスクリプト。

    --- 処理アルゴリズム ---
    このスクリプトは、大きく分けて2つのパスで処理を行います。

    [パス1: 情報収集と画像パートの修正]
    1. MHTMLファイル内の全MIMEパートをスキャンします。
    2. パートが画像の場合、その`Content-Location`ヘッダー（元のファイル名）を取得します。
    3. ファイル名に基づき、他のIDと重複しないユニークな`Content-ID`を生成します。
       - この際、IDとして不正な文字はアンダースコアに置換し、先頭のハイフンは接頭辞を付けて回避します。
    4. 「元の参照（Content-Locationや古いContent-ID）」と「新しいContent-ID」を対応付けるマップを作成します。
    5. 画像パートのヘッダーを、生成した新しい`Content-ID`に書き換えます。

    [パス2: HTMLパートの修正]
    1. HTMLパートを特定し、そのボディ部分を適切なエンコーディング（quoted-printableなど）でデコードします。
    2. HTMLボディ内のすべての`<img>`タグを正規表現で検索します。
    3. 各`<img>`タグについて、以下の処理を行います。
       a. `src`属性の値を取得します。
       b. パス1で作成したマップを使い、`src`に対応する新しい`Content-ID`を検索します。
       c. `<img>`タグの構造（alt属性など）は維持したまま、`src`属性のみを新しい`cid:`参照に置換します。
       d. タグの末尾が自己終端形式 (`/>`) になるように保証します。
    4. 修正が完了したHTMLボディを、元のエンコーディング形式に再エンコードします。

    [最終処理: ファイルの再構築]
    1. 修正された各パートを、MIMEのboundaryで再度結合し、新しいMHTMLファイルとして保存します。

    --- 機能概要 ---
    - 各画像にユニークで有効なContent-IDを付与する。
    - HTML内のすべての`<img>`タグを維持し、src="..."を対応する"cid:..."に書き換える。
    - すべての`<img>`タグを自己終端形式 (/>) にする。
    - quoted-printableエンコーディングに正しく対応する。
    """

    # --- 初期設定とファイル読み込み ---
    # 入力ファイルが存在するかチェック
    if not os.path.exists(input_file):
        print(f"エラー: ファイルが見つかりません: {input_file}")
        return

    # 出力ファイル名を生成 (例: test.mhtml -> test_fixed.mhtml)
    base, ext = os.path.splitext(input_file)
    output_file = base + "_fixed" + ext

    print(f"入力ファイル: {input_file}")
    print(f"出力ファイル: {output_file}\n")

    # MHTMLファイルをバイナリモードで読み込む
    try:
        with open(input_file, "rb") as f:
            content = f.read()
    except IOError as e:
        print(f"エラー: ファイルを読み込めませんでした: {e}")
        return

    # --- 1. MHTMLの解析: Boundaryの特定とパート分割 ---
    # MHTMLのヘッダーからMIMEパートを区切るboundary文字列を正規表現で取得
    boundary_match = re.search(br'boundary="([^"]+)"', content, re.IGNORECASE)
    if not boundary_match:
        print("エラー: MHTMLのboundaryが見つかりませんでした。")
        return
    boundary = b"--" + boundary_match.group(1)
    print(f"MIME Boundaryを特定: {boundary.decode()}\n")

    # boundaryでファイル全体を分割し、各パートのリストを作成
    parts = content.split(boundary)
    header_part = parts[0]       # ファイル全体のヘッダー
    content_parts = parts[1:-1]  # 中間のMIMEパート（HTMLや画像など）

    # --- 処理用変数の初期化 ---
    # 新旧の参照情報を紐付けるための辞書
    ref_map = {}  # {旧参照(Content-Location/旧CID) -> 新CID}
    # HTMLパートの情報を格納するための辞書
    html_part_info = {}
    # 修正後のパートを格納するためのリスト
    modified_content_parts = []

    print("--- パス1: 画像パーツをスキャンし、新しいContent-IDを生成 ---")

    # --- 2. 各パーツを処理: Content-IDを再生成し、HTMLパートの情報を控える ---
    for i, part_bytes in enumerate(content_parts):
        # パートが画像かどうかを判定
        if b'Content-Type: image' in part_bytes:
            print(f"\n画像パート {i+1} を処理中...")
            # Content-Locationヘッダーから元のファイル名を取得
            loc_match = re.search(br"Content-Location: ?([^\r\n]+)", part_bytes, re.IGNORECASE)
            if not loc_match:
                modified_content_parts.append(part_bytes)
                continue

            original_location = loc_match.group(1).strip().decode('utf-8', 'ignore')
            filename = os.path.basename(original_location)
            print(f"  - 元ファイル名: {filename}")

            # Content-IDとして有効な文字列にサニタイズ（不正文字を置換）
            sanitized_filename = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
            # 先頭のハイフンは不正なCIDの原因となるため、接頭辞を追加して回避
            if sanitized_filename.startswith('-'):
                sanitized_filename = 'image' + sanitized_filename
                print(f"  - (修正) 先頭ハイフンを回避: {sanitized_filename}")

            # UUIDを加えて一意性を保証した新しいContent-IDを生成
            new_cid = f"{sanitized_filename}.{uuid.uuid4().hex[:8]}@mhtml.fixer"
            print(f"  - 新しいContent-IDを生成: {new_cid}")

            # 新旧の参照情報をマッピング辞書に保存
            ref_map[original_location] = new_cid

            modified_part = part_bytes
            # もし古いContent-IDが存在すれば、それもマッピング対象に追加
            old_cid_match = re.search(br"Content-ID: ?<([^>]+)>", modified_part, re.IGNORECASE)
            if old_cid_match:
                old_cid = old_cid_match.group(1).strip().decode('utf-8', 'ignore')
                ref_map[old_cid] = new_cid
                print(f"  - 古いContent-IDをマッピング: {old_cid} -> {new_cid}")
                # パートのヘッダーを新しいContent-IDで置換
                modified_part = re.sub(
                    br"Content-ID: ?<[^>]+>", f"Content-ID: <{new_cid}>".encode('ascii'),
                    modified_part, count=1, flags=re.IGNORECASE)
            else:
                # Content-IDが存在しない場合は、Content-Locationの下に追記
                modified_part = re.sub(
                    br"(Content-Location: ?[^\r\n]+)",
                    br"\1\r\nContent-ID: <" + new_cid.encode('ascii') + b">",
                    modified_part, count=1, flags=re.IGNORECASE)

            modified_content_parts.append(modified_part)
        
        # パートがHTMLの場合
        elif b"Content-Type: text/html" in part_bytes:
            # エンコーディング（quoted-printableなど）を特定
            encoding_match = re.search(br'Content-Transfer-Encoding: ?([^\r\n]+)', part_bytes, re.IGNORECASE)
            encoding = encoding_match.group(1).strip().lower() if encoding_match else b'7bit'
            # 後で処理するためにHTMLパートの情報を保存
            html_part_info = {'index': len(modified_content_parts), 'encoding': encoding, 'bytes': part_bytes}
            modified_content_parts.append(part_bytes) # この時点では未修正のまま追加
        
        # 画像でもHTMLでもないその他のパート
        else:
            modified_content_parts.append(part_bytes)

    print("\n--- パス2: HTML内の<img>タグ参照を 'cid:' 形式に更新 ---")

    if not html_part_info:
        print("エラー: HTMLパートが見つかりませんでした。")
        return

    # --- 3. HTMLパートの参照を更新 ---
    html_part_bytes = html_part_info['bytes']
    
    # HTMLパートをヘッダーとボディに分割
    header_end_match = re.search(b'\r?\n\r?\n', html_part_bytes)
    header_end_pos = header_end_match.end()
    html_headers = html_part_bytes[:header_end_pos]
    html_body_encoded = html_part_bytes[header_end_pos:]

    # エンコーディングに応じてボディをデコード
    if html_part_info['encoding'] == b'quoted-printable':
        html_body_decoded = quopri.decodestring(html_body_encoded)
    else: # 7bit, 8bit, binaryなどの場合はそのまま
        html_body_decoded = html_body_encoded

    # re.subで利用するコールバック関数を定義
    def fix_img_tag(match):
        img_tag_bytes = match.group(0) # マッチした<img>タグ全体

        # <img>タグ全体からsrc属性を正規表現で抽出
        src_match = re.search(br'src\s*=\s*(["\']?)([^"\' >]+)\1', img_tag_bytes, re.IGNORECASE)
        if not src_match:
            # src属性がない場合は、自己終端タグの修正のみ行う
            if img_tag_bytes.endswith(b'/>'): return img_tag_bytes
            if img_tag_bytes.endswith(b'>'): return img_tag_bytes[:-1] + b'/>'
            return img_tag_bytes

        src_value = src_match.group(2).decode('utf-8', 'ignore')

        # マッピング辞書を使って、srcに対応する新しいContent-IDを検索
        key_to_check = src_value[4:] if src_value.lower().startswith('cid:') else src_value
        new_cid = ref_map.get(key_to_check)

        # もしフルパスで見つからなければ、ファイル名だけで再検索
        if not new_cid:
            basename_to_check = os.path.basename(key_to_check)
            for loc, cid_val in ref_map.items():
                if os.path.basename(loc) == basename_to_check:
                    new_cid = cid_val
                    break
        
        modified_tag = img_tag_bytes
        if new_cid:
            # src属性のみを新しいcid参照に置換
            new_src_attr = f'src="cid:{new_cid}"'.encode('utf-8')
            modified_tag = re.sub(br'src\s*=\s*(["\']?)[^"\' >]+\1', new_src_attr, img_tag_bytes, count=1, flags=re.IGNORECASE)
            print(f"  - 参照を更新: {src_value} -> cid:{new_cid}")
        else:
            print(f"  - 警告: 参照 '{src_value}' に対応する画像パートが見つかりません。")

        # 最後に、タグが /> で終わるように保証する
        if modified_tag.endswith(b'/>'):
            return modified_tag
        if modified_tag.endswith(b'>'):
            return modified_tag[:-1] + b'/>'
        
        return modified_tag + b'/>' # 万が一のためのフォールバック

    # HTMLボディ内の全<img>タグに対して置換処理を実行
    html_body_modified = re.sub(
        br'<img[^>]+>', # <img>タグ全体にマッチ
        fix_img_tag,
        html_body_decoded,
        flags=re.IGNORECASE
    )

    # 修正したHTMLボディを元のエンコーディング形式に再エンコード
    if html_part_info['encoding'] == b'quoted-printable':
        final_html_body = quopri.encodestring(html_body_modified)
    else:
        final_html_body = html_body_modified
    
    # 修正済みHTMLパートをリストに戻す
    modified_content_parts[html_part_info['index']] = html_headers + final_html_body

    # --- 4. 全てのパートを再結合してファイルに書き込む ---
    print("\n--- パス3: 修正済みMHTMLファイルを生成 ---")
    try:
        with open(output_file, "wb") as f:
            # ファイル全体のヘッダーを書き込み
            f.write(header_part)
            # 修正済みの各パートをboundaryで区切りながら書き込み
            for part in modified_content_parts:
                f.write(boundary)
                if not part.startswith(b"\r\n"): f.write(b"\r\n")
                f.write(part)
            
            # 終了のboundaryを書き込み
            f.write(boundary + b"--\r\n")
        print(f"\n処理完了。修正済みファイルが {output_file} に保存されました。")
    except IOError as e:
        print(f"エラー: ファイルの書き込みに失敗しました: {e}")

# --- スクリプトのエントリーポイント ---
if __name__ == "__main__":
    # コマンドライン引数が正しいかチェック
    if len(sys.argv) != 2:
        print("使い方: python fix_mhtml_imge.py <input.mhtml>")
        sys.exit(1)
    
    # メインの処理関数を呼び出し
    fix_mhtml_image_references(sys.argv[1])
