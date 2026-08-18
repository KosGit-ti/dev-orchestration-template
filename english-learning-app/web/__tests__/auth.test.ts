import { LocalAuthProvider, SEED_ACCOUNT_HINTS } from "@/lib/auth/local-provider";
import { hashPassword, verifyPassword, generateToken } from "@/lib/auth/crypto";
import { isAcceptablePassword, MIN_PASSWORD_LENGTH } from "@/lib/auth/types";

// PBKDF2 を 600,000 回まわすため、既定の 5 秒では足りない。
jest.setTimeout(60_000);

describe("パスワードハッシュ", () => {
  it("同じパスワードでも毎回ソルトが変わる", async () => {
    const a = await hashPassword("correct battery staple");
    const b = await hashPassword("correct battery staple");
    expect(a.salt).not.toBe(b.salt);
    expect(a.hash).not.toBe(b.hash);
  });

  it("正しいパスワードだけを受け入れる", async () => {
    const stored = await hashPassword("correct battery staple");
    await expect(verifyPassword("correct battery staple", stored)).resolves.toBe(true);
    await expect(verifyPassword("correct battery stapl", stored)).resolves.toBe(false);
    await expect(verifyPassword("", stored)).resolves.toBe(false);
  });

  it("ハッシュに素のパスワードが残らない", async () => {
    const stored = await hashPassword("plaintext-secret");
    expect(JSON.stringify(stored)).not.toContain("plaintext-secret");
    expect(stored.algorithm).toBe("PBKDF2-SHA256");
    expect(stored.iterations).toBeGreaterThanOrEqual(600_000);
  });

  it("トークンは毎回異なる", () => {
    expect(generateToken()).not.toBe(generateToken());
  });

  it("短いパスワードを弾く", () => {
    expect(isAcceptablePassword("a".repeat(MIN_PASSWORD_LENGTH))).toBe(true);
    expect(isAcceptablePassword("a".repeat(MIN_PASSWORD_LENGTH - 1))).toBe(false);
    expect(isAcceptablePassword("   short   ")).toBe(false);
  });
});

describe("クライアント完結の認証", () => {
  let provider: LocalAuthProvider;

  beforeEach(async () => {
    window.localStorage.clear();
    provider = new LocalAuthProvider();
    await provider.bootstrap();
  });

  it("初期アカウントとして admin と shunsuke を作る", async () => {
    const admin = await provider.signIn("admin", "change-me-admin");
    expect(admin.ok).toBe(true);
    if (admin.ok) {
      expect(admin.value.role).toBe("admin");
      expect(admin.value.mustChangePassword).toBe(true);
    }
    await provider.signOut();
    const learner = await provider.signIn("shunsuke", "change-me-shunsuke");
    expect(learner.ok).toBe(true);
    if (learner.ok) {
      expect(learner.value.role).toBe("learner");
    }
  });

  it("初期アカウントのヒントは実際に通るパスワードを示す", async () => {
    for (const hint of SEED_ACCOUNT_HINTS) {
      const result = await provider.signIn(hint.username, hint.initialPassword);
      expect(result.ok).toBe(true);
      await provider.signOut();
    }
  });

  it("bootstrap は冪等で、二度呼んでもアカウントが増えない", async () => {
    await provider.bootstrap();
    await provider.signIn("admin", "change-me-admin");
    const admin = await provider.currentUser();
    const list = await provider.listAccounts(admin!.id);
    expect(list.ok).toBe(true);
    if (list.ok) {
      expect(list.value).toHaveLength(SEED_ACCOUNT_HINTS.length);
    }
  });

  it("ユーザー名の大文字小文字を区別しない", async () => {
    await expect(provider.signIn("ADMIN", "change-me-admin")).resolves.toMatchObject({ ok: true });
  });

  it("誤ったパスワードと存在しないユーザーで同じ理由を返す", async () => {
    const wrongPassword = await provider.signIn("admin", "nope");
    const noSuchUser = await provider.signIn("nobody", "nope");
    expect(wrongPassword).toEqual({ ok: false, reason: "invalid-credentials" });
    expect(noSuchUser).toEqual({ ok: false, reason: "invalid-credentials" });
  });

  it("パスワード変更で mustChangePassword が下りる", async () => {
    const signedIn = await provider.signIn("shunsuke", "change-me-shunsuke");
    expect(signedIn.ok).toBe(true);
    if (!signedIn.ok) {
      return;
    }
    const changed = await provider.changePassword(
      signedIn.value.id,
      "change-me-shunsuke",
      "a-long-enough-password",
    );
    expect(changed.ok).toBe(true);
    if (changed.ok) {
      expect(changed.value.mustChangePassword).toBe(false);
    }
    await expect(provider.signIn("shunsuke", "a-long-enough-password")).resolves.toMatchObject({
      ok: true,
    });
    await expect(provider.signIn("shunsuke", "change-me-shunsuke")).resolves.toMatchObject({
      ok: false,
    });
  });

  it("現在のパスワードが違えば変更を拒む", async () => {
    const signedIn = await provider.signIn("admin", "change-me-admin");
    if (!signedIn.ok) {
      throw new Error("前提となるログインに失敗した");
    }
    await expect(
      provider.changePassword(signedIn.value.id, "wrong", "a-long-enough-password"),
    ).resolves.toEqual({ ok: false, reason: "invalid-credentials" });
  });

  it("短すぎる新パスワードを拒む", async () => {
    const signedIn = await provider.signIn("admin", "change-me-admin");
    if (!signedIn.ok) {
      throw new Error("前提となるログインに失敗した");
    }
    await expect(
      provider.changePassword(signedIn.value.id, "change-me-admin", "short"),
    ).resolves.toEqual({ ok: false, reason: "weak-password" });
  });

  it("学習者は管理操作を行えない", async () => {
    const learner = await provider.signIn("shunsuke", "change-me-shunsuke");
    if (!learner.ok) {
      throw new Error("前提となるログインに失敗した");
    }
    await expect(provider.listAccounts(learner.value.id)).resolves.toEqual({
      ok: false,
      reason: "forbidden",
    });
    await expect(
      provider.createAccount(learner.value.id, {
        username: "x",
        displayName: "X",
        role: "admin",
        password: "a-long-enough-password",
      }),
    ).resolves.toEqual({ ok: false, reason: "forbidden" });
  });

  it("管理者はアカウントを追加・削除できる", async () => {
    const admin = await provider.signIn("admin", "change-me-admin");
    if (!admin.ok) {
      throw new Error("前提となるログインに失敗した");
    }
    const created = await provider.createAccount(admin.value.id, {
      username: "Kenji",
      displayName: "Kenji",
      role: "learner",
      password: "a-long-enough-password",
    });
    expect(created.ok).toBe(true);
    if (!created.ok) {
      return;
    }
    expect(created.value.username).toBe("kenji");
    await expect(
      provider.createAccount(admin.value.id, {
        username: "kenji",
        displayName: "dup",
        role: "learner",
        password: "a-long-enough-password",
      }),
    ).resolves.toEqual({ ok: false, reason: "username-taken" });

    await expect(provider.deleteAccount(admin.value.id, created.value.id)).resolves.toEqual({
      ok: true,
      value: true,
    });
  });

  it("管理者は自分自身を削除できない", async () => {
    const admin = await provider.signIn("admin", "change-me-admin");
    if (!admin.ok) {
      throw new Error("前提となるログインに失敗した");
    }
    await expect(provider.deleteAccount(admin.value.id, admin.value.id)).resolves.toEqual({
      ok: false,
      reason: "forbidden",
    });
  });

  it("パスワード再設定は再度の変更を強制する", async () => {
    const admin = await provider.signIn("admin", "change-me-admin");
    if (!admin.ok) {
      throw new Error("前提となるログインに失敗した");
    }
    const target = await provider.listAccounts(admin.value.id);
    if (!target.ok) {
      throw new Error("一覧取得に失敗した");
    }
    const learner = target.value.find((account) => account.username === "shunsuke")!;
    const reset = await provider.resetPassword(admin.value.id, learner.id, "temporary-password");
    expect(reset.ok).toBe(true);
    if (reset.ok) {
      expect(reset.value.mustChangePassword).toBe(true);
    }
    await expect(provider.signIn("shunsuke", "temporary-password")).resolves.toMatchObject({
      ok: true,
    });
  });

  it("ログアウトすると現在の利用者が消える", async () => {
    await provider.signIn("admin", "change-me-admin");
    expect(await provider.currentUser()).not.toBeNull();
    await provider.signOut();
    expect(await provider.currentUser()).toBeNull();
  });

  it("レベルを保存して復元できる", async () => {
    const signedIn = await provider.signIn("shunsuke", "change-me-shunsuke");
    if (!signedIn.ok) {
      throw new Error("前提となるログインに失敗した");
    }
    expect(signedIn.value.level).toBeNull();
    await provider.updateLevel(signedIn.value.id, 4);
    expect((await provider.currentUser())?.level).toBe(4);
  });
});
