FROM alpine:3.19

# Instalar Java (para rodar JMeter) e Python (para o relatório)
RUN apk add --no-cache openjdk21-jre wget curl python3 bash && \
    ln -sf python3 /usr/bin/python

# Instalar JMeter 5.6.3
ENV JMETER_VERSION=5.6.3
ENV JMETER_HOME=/opt/jmeter
ENV PATH=$JMETER_HOME/bin:$PATH

RUN wget -q "https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-${JMETER_VERSION}.tgz" -O /tmp/jmeter.tgz && \
    mkdir -p $JMETER_HOME && \
    tar -xzf /tmp/jmeter.tgz -C /opt && \
    mv /opt/apache-jmeter-${JMETER_VERSION}/* $JMETER_HOME && \
    rm -rf /tmp/jmeter.tgz

# Plugin de geração de relatório dashboard
RUN wget -q "https://repo1.maven.org/maven2/org/apache/jmeter/jmeter/5.6.3/jmeter-5.6.3.jar" \
    -O /tmp/check.jar 2>/dev/null || true

WORKDIR /jmeter

COPY benchmark.jmx .
COPY report.py .
COPY prom_query.py .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

VOLUME ["/results"]

ENTRYPOINT ["/jmeter/entrypoint.sh"]
