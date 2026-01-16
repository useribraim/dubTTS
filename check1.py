import boto3
translate = boto3.client("translate", region_name="eu-west-1")
try:
    resp = translate.translate_text(
        Text="Hello world",
        SourceLanguageCode="en",
        TargetLanguageCode="ru"
    )
    print("[OK] Translation works!")
    print(f"Result: {resp['TranslatedText']}")
except Exception as e:
    print(f"[ERROR] Translation failed: {e}")