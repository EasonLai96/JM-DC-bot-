# -*- coding: utf-8 -*-
import os
import aiohttp

async def upload_to_pixeldrain(file_path: str) -> str:
    """
    將指定檔案上傳至 Pixeldrain 免空，成功則返回下載 URL，失敗拋出 Exception。
    """
    url = "https://pixeldrain.com/api/file"
    file_name = os.path.basename(file_path)
    
    api_key = os.getenv("PIXELDRAIN_API_KEY", "")
    auth = aiohttp.BasicAuth(login="api", password=api_key) if api_key else None

    # 流式上傳，防止大檔案擠爆 512MB 記憶體
    data = aiohttp.FormData()
    data.add_field('file', open(file_path, 'rb'), filename=file_name)
    
    timeout = aiohttp.ClientTimeout(total=1800) # 30分鐘逾時上限
    
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, data=data, auth=auth) as response:
            if response.status in [200, 201]:
                res_json = await response.json()
                if res_json.get("success"):
                    file_id = res_json.get("id")
                    return f"https://pixeldrain.com/u/{file_id}"
                else:
                    raise Exception(f"Pixeldrain 回傳失敗: {res_json.get('message')}")
            else:
                text = await response.text()
                raise Exception(f"Pixeldrain 錯誤 ({response.status}): {text}")