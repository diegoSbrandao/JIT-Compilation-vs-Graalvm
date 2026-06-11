# ─── Stage 1: Build com GraalVM + native-image ────────────────────────────
FROM ghcr.io/graalvm/native-image-community:21 AS build

WORKDIR /app

RUN microdnf install -y maven && microdnf clean all

# Cacheia download de dependências
COPY pom.xml .
RUN mvn -q dependency:go-offline -DskipTests

COPY src ./src

# Sequência obrigatória:
# 1. compile        — compila .java → .class (sem isso, process-aot falha com ClassNotFoundException)
# 2. process-aot    — Spring analisa beans e gera reflect-config.json, resource-config.json etc.
# 3. compile-no-fork — GraalVM usa os metadados do step anterior para gerar o binário nativo
RUN mvn -q compile && \
    mvn -q spring-boot:process-aot && \
    mvn -q native:compile-no-fork -DskipTests

# ─── Stage 2: Runtime ultra-leve (sem JVM) ────────────────────────────────
FROM ubuntu:22.04

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libstdc++6 \
        zlib1g \
        wget \
        curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=build /app/target/app-native ./app-native
RUN chmod +x ./app-native

EXPOSE 8081
ENTRYPOINT ["./app-native"]
