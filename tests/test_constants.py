"""Constantes partilhadas nos testes.

Segredo HS256 com >= 32 bytes para satisfazer PyJWT e evitar InsecureKeyLengthWarning.
"""

TEST_JWT_HS256_SECRET = "py-payments-test-jwt-hs256-key-32bytes!!"
