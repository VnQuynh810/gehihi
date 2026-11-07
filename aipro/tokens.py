import re

# Google One Offer Tokens (Universal across accounts/currencies)
TOK_PRO5TB_12M   = "AVbCH/cspB8VdqKJ+ax/uPxUFAVuYRPX4iSjtg4Fu0PG9zUeURSM7y//LlQva1dCd/rbKVM6EJmyiG/C9BoQ95pMl8yDCHuiwCvM5CuGJME="
TOK_PRO5TB_6M    = "AVbCH/fUON3w8hpfeutprA3BMQwrBwYIB6Y8CSWi1isDCDswKUUhGQ3dluj5WXsNKOvr8K5UULBGrRsf4KlW1rHLz0fF3SvxQhBFRbBOHCA="
TOK_PRO5TB_6M_2  = "AVbCH/cJ5SeHgkVRt+bdcrapx/aLSu2kbsBgMdI413zbllDNsNQoC/zVlG/Npbu55WlMzBwWPhUedIvZFDg7fw0CmCv/8DKj04IZ21pK7mrV/hh5"
TOK_PRO5TB_6M_SS = "AVbCH/cgl4zvPT2Lqw95Gn+RYAJIVwFwi783i6uVprX7u3MCa5J4CcTXH5RABa5WjBrUoQnwf8vI10TNV7ktd26myIdxqIwGmNoJ5ofYNqpQ10RKoSvHs75wsfA="
TOK_P1M          = "AVbCH/dXbuvFhoeuGZb/+3+b5Jhft4kOY/wQLGqLhy0hhx6hDAJ/1z9y23+x9N5zyNtQ268DEw=="
TOK_MONTHLY      = "AVbCH/dSbs1UHItdkpohleJ9+69eSPWyFRutIpRUdGPlsdDKw2IMvRRHiwDefRVyEHl12jqfmBgQZByeX7hJ"
YT_FREE_TOK      = "AVbCH/eW4Nfp8KBl0zlm7x9PhaiCvpnqwUuL2D8NFPBQgmM9+HuYS1h2xH5lLWU/BsBj775LK5GqK8UHKqoSzhDDz+PqP3paNesqLU0r9i6XCrYa"
PREFIX           = "subs:com.google.android.apps.subscriptions.red:"

# Exact zero-price boundary pattern (matches "$0.00", "US$0.00", "0.00", etc., but rejects "$10.00" or "$20.00")
_ZERO_PRICE_RE = re.compile(
    r"(?<![0-9.])(?:US\$|HK\$|CA\$|A\$|NT\$|S\$|\$|€|£|¥|￥|₩|₹|R\$|CHF|SEK|NOK|DKK|PLN|TRY|IDR|PHP|THB|MYR|VND)?\s*0[.,]00(?!\d)",
    re.IGNORECASE,
)
