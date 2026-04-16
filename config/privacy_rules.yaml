builtin_entities:
  - PERSON
  - EMAIL_ADDRESS
  - PHONE_NUMBER
  - CREDIT_CARD
  - IBAN_CODE
  - IP_ADDRESS
  - URL

custom_patterns:
  - name: API_KEY
    regex: '(?:sk-|api[_-]?key[=:\s]+)[A-Za-z0-9_\-]{20,}'
    score: 0.9
  - name: PASSWORD
    regex: '(?:password|passwd|pwd)[=:\s]+\S+'
    score: 0.85
  - name: AWS_SECRET
    regex: '(?:AKIA|aws_secret)[A-Za-z0-9/+=]{16,}'
    score: 0.95
  - name: PRIVATE_KEY
    regex: '-----BEGIN (?:RSA |EC )?PRIVATE KEY-----'
    score: 0.99
