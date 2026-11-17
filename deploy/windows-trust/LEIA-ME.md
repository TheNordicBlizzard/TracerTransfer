# Confiança no certificado interno de assinatura

Use quando os executáveis do TracerTransfer forem assinados com um certificado
**interno** da sua organização (gratuito, autoassinado), em vez de um certificado
comercial.

## O que isso resolve

- Nos computadores que confiam no certificado, os `.exe` aparecem como **assinados**
  pela sua organização, e o Windows acusa qualquer alteração no arquivo.
- Fora desses computadores, a assinatura continua "não confiável".
- O SmartScreen ainda pode avisar em arquivos **baixados pelo navegador**. Por isso,
  prefira copiar pela rede interna ou usar a atualização automática do cliente.

## Instalar em um computador (uma única vez)

Abra o **PowerShell como Administrador** na pasta que contém o `.cer` e este script:

```powershell
.\Instalar-Certificado.ps1 -CertificatePath .\TracerTransfer-CodeSigning.cer -ExpectedThumbprint <THUMBPRINT>
```

O thumbprint deve ser informado pelo administrador por um canal separado (não confie
num valor que veio junto com o arquivo). O script **recusa** o certificado se:

- o thumbprint não conferir;
- o arquivo contiver chave privada;
- o certificado puder emitir outros certificados (CA);
- o certificado tiver qualquer uso além de "Code Signing".

Com essas restrições, confiar no certificado não permite interceptar HTTPS.

## Remover

```powershell
.\Instalar-Certificado.ps1 -CertificatePath .\TracerTransfer-CodeSigning.cer -ExpectedThumbprint <THUMBPRINT> -Remove
```

## Assinar (máquina de build)

```powershell
.\scripts\sign_windows.ps1 -Thumbprint <THUMBPRINT> -Path .\dist\<versão>\TracerCliente.exe, .\dist\<versão>\TracerEntregador.exe
```

A chave privada fica no repositório de certificados do Windows do usuário de build
(não exportável). Nenhum arquivo PFX ou senha é usado. Toda assinatura recebe carimbo
de tempo RFC 3161; por isso, continua válida depois que o certificado expirar.
