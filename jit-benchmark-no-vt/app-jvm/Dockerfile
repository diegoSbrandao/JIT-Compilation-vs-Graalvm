# ─── Stage 1: Build ────────────────────────────────────────────────────────
FROM eclipse-temurin:21-jdk-alpine AS build

WORKDIR /app
COPY pom.xml .
COPY src ./src

RUN apk add --no-cache maven && \
    mvn -q package -DskipTests

# ─── Stage 2: Runtime ──────────────────────────────────────────────────────
FROM eclipse-temurin:21-jre-alpine

WORKDIR /app
COPY --from=build /app/target/*.jar app.jar

# JVM clássica — sem flags especiais, HotSpot puro com Tiered Compilation
# PrintCompilation ativo para visualizar o JIT no log
ENV JAVA_OPTS="-Xms256m -Xmx512m \
               -XX:+TieredCompilation \
               -XX:ReservedCodeCacheSize=128m \
               -XX:+PrintCompilation \
               -XX:+UnlockDiagnosticVMOptions"

EXPOSE 8080

ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar app.jar"]
