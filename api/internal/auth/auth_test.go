package auth

import "testing"

// Hash produced by scripts/dynamo/build_import.py hash_password("demo1234", 1000).
const pyHash = "pbkdf2_sha256$1000$WYTe6CgiDDcYo+J28p/FDw==$JFtRpFcqGiXVi8IilLgLGZxDM4NXedQkmn1ShidCOtw="

func TestVerifyPythonHash(t *testing.T) {
	if !VerifyPassword(pyHash, "demo1234") {
		t.Fatal("valid password rejected")
	}
	if VerifyPassword(pyHash, "demo12345") {
		t.Fatal("invalid password accepted")
	}
}

func TestTokenRoundTrip(t *testing.T) {
	t.Setenv("JWT_SECRET", "test-secret")
	tok, err := sign(Claims{Sub: "u_01", JTI: "ses_1", EXP: 1 << 40})
	if err != nil {
		t.Fatal(err)
	}
	if c, err := parse(tok); err != nil || c.Sub != "u_01" {
		t.Fatalf("parse: %v %v", c, err)
	}
	if _, err := parse(tok + "x"); err == nil {
		t.Fatal("tampered token accepted")
	}
	expired, _ := sign(Claims{Sub: "u_01", JTI: "ses_2", EXP: 1})
	if _, err := parse(expired); err != ErrExpired {
		t.Fatalf("want ErrExpired, got %v", err)
	}
}
