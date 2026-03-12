# 🍽️ Sistema de Comandas — Servidor Local
## Guia de Instalação e Uso

---

## 📁 Estrutura de Arquivos

```
pesqueiro-server/
├── INICIAR.bat          ← Clique duplo para iniciar (Windows)
├── INICIAR_MAC.command  ← Clique duplo para iniciar (Mac)
├── servidor.py          ← O servidor (não mexa)
├── static/
│   └── app.html         ← App do restaurante (não mexa)
└── data/                ← Criada automaticamente com todos os dados
    ├── master.json
    ├── plans.json
    └── restaurants/
        └── [id]/        ← Dados de cada restaurante separados
```

---

## 🚀 Como Iniciar

### Windows:
1. **Clique duplo** em `INICIAR.bat`
2. Uma janela preta vai abrir — **não feche ela**
3. O navegador abre automaticamente em `http://localhost:8080`

### Mac:
1. Clique com botão direito em `INICIAR_MAC.command`
2. Clique em "Abrir"
3. O navegador abre automaticamente

### Se não abrir automaticamente:
Acesse manualmente: **http://localhost:8080**

---

## 🔐 Acesso ao Painel Master (Você)

- **Endereço:** `http://localhost:8080` ou `http://SEU_IP:8080`
- **Usuário:** `admin`
- **Senha:** `admin123`

> ⚠️ **Troque a senha** em Configurações após o primeiro acesso!

---

## 🏪 Como Cadastrar um Restaurante

1. Acesse o **Painel Master**
2. Clique em **"＋ Novo Restaurante"**
3. Preencha: nome, responsável, plano, senha ADM
4. Após criar, um **link único** aparece na lista:
   ```
   http://SEU_IP:8080/r/abc123xyz
   ```
5. Envie esse link para o restaurante acessar via celular/tablet

---

## 📱 Como o Restaurante Acessa

O restaurante precisa estar na **mesma rede Wi-Fi** que o seu computador.

1. Descobrir seu IP: abra o terminal e digite:
   - Windows: `ipconfig` (procure "Endereço IPv4")
   - Mac: `ifconfig | grep inet`
2. O link do restaurante será: `http://192.168.X.X:8080/r/[id]`
3. Esse link aparece no Painel Master ao clicar em "👁 Ver" do restaurante
4. O garçom/caixa acessa pelo celular na mesma rede Wi-Fi

---

## 💾 Dados

- Todos os dados ficam salvos na pasta `data/`
- **Faça backup desta pasta regularmente!**
- Cada restaurante tem sua pasta separada: `data/restaurants/[id]/`
- Os dados **não se perdem** ao fechar e reabrir o servidor

---

## 🔧 Resolução de Problemas

**"Python não encontrado"**
→ Instale Python em: https://python.org/downloads
→ Marque "Add Python to PATH" durante a instalação

**"Porta 8080 em uso"**
→ Edite `servidor.py` linha 4, mude `PORT = 8080` para `PORT = 8081`

**Restaurante não consegue acessar**
→ Verifique se estão na mesma rede Wi-Fi
→ Verifique se o firewall do Windows não está bloqueando
→ Windows: Painel de Controle → Firewall → Permitir Python

**Servidor fecha sozinho**
→ Não feche a janela preta — ela precisa ficar aberta

---

## 📞 Fluxo Resumido

```
Você (Painel Master)        Restaurante (Celular)
        |                           |
  Cadastra restaurante         Recebe o link
        |                           |
  Define plano/status          Acessa pelo Wi-Fi
        |                           |
  Monitora em tempo real       Faz comandas normalmente
        |                           |
  Suspende/Cancela se          Sistema bloqueia acesso
  necessário                   automaticamente
```

---

*Sistema desenvolvido para funcionar 100% offline em rede local.*
*Nenhum dado sai do seu computador.*

---

## ▶️ Como Iniciar no Windows (atualizado)

### Opção 1 — Arquivo VBS (recomendado):
1. Clique duplo em **`INICIAR.vbs`**
2. Se aparecer aviso, clique em **"Mais informações" → "Executar assim mesmo"**

### Opção 2 — PowerShell:
1. Clique com botão direito em **`INICIAR.ps1`**
2. Clique em **"Executar com PowerShell"**
3. Se pedir confirmação, digite `S` e Enter

### Opção 3 — Desbloquear o .bat manualmente:
1. Clique com botão direito no `INICIAR.bat`
2. Clique em **Propriedades**
3. Na parte de baixo marque **"Desbloquear"**
4. Clique OK → agora pode clicar duplo normalmente
